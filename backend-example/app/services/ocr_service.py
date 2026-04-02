from datetime import date, datetime
from decimal import Decimal
import mimetypes

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
import logging
from ..models.receipt import Document, OcrRun, ReceiptExtraction


class OcrService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _normalize_tax_rate_label(value: object) -> str:
        text = str(value or "").strip().lower().replace("％", "%")
        if text in {"8", "8%"}:
            return "8"
        if text in {"10", "10%"}:
            return "10"
        if text == "mixed":
            return "mixed"
        return "unknown"

    async def process_document(self, document: Document) -> tuple[OcrRun, ReceiptExtraction]:
        provider_used = "paddle-ocr"
        run = OcrRun(
            document_id=document.id,
            provider=provider_used,
            provider_model="default",
            run_status="running",
        )
        self.db.add(run)
        await self.db.flush()

        normalized, provider_used = await self._extract(document.storage_path)
        payload_json = self._to_jsonable(normalized)
        normalized_tax_rate_label = self._normalize_tax_rate_label(normalized.get("tax_rate_label", "unknown"))
        run.provider = provider_used

        run.run_status = "completed"
        run.confidence_score = float(normalized["confidence"])
        run.raw_text = normalized.get("raw_text")
        run.raw_payload = payload_json

        existing = (
            await self.db.execute(
                select(ReceiptExtraction).where(ReceiptExtraction.document_id == document.id)
            )
        ).scalars().first()

        if existing is not None:
            existing.ocr_run_id = run.id
            existing.transaction_date = self._to_date(normalized.get("transaction_date"))
            existing.merchant_name = normalized.get("merchant_name")
            existing.merchant_phone = normalized.get("telephone")
            existing.merchant_address = normalized.get("store_info")
            existing.registration_number = normalized.get("registration_number")
            existing.subtotal_excl_tax = Decimal(str(normalized.get("subtotal_excl_tax", 0)))
            existing.tax_amount = Decimal(str(normalized.get("tax_amount", 0)))
            existing.total_amount = Decimal(str(normalized.get("total_amount", 0)))
            existing.currency_code = "JPY"
            existing.payment_method = normalized.get("payment_method")
            existing.tax_rate_label = normalized_tax_rate_label
            existing.normalized_payload = payload_json
            existing.extraction_confidence = float(normalized["confidence"])
            extraction = existing
        else:
            extraction = ReceiptExtraction(
                document_id=document.id,
                ocr_run_id=run.id,
                transaction_date=self._to_date(normalized.get("transaction_date")),
                merchant_name=normalized.get("merchant_name"),
                merchant_phone=normalized.get("telephone"),
                merchant_address=normalized.get("store_info"),
                registration_number=normalized.get("registration_number"),
                subtotal_excl_tax=Decimal(str(normalized.get("subtotal_excl_tax", 0))),
                tax_amount=Decimal(str(normalized.get("tax_amount", 0))),
                total_amount=Decimal(str(normalized.get("total_amount", 0))),
                currency_code="JPY",
                payment_method=normalized.get("payment_method"),
                tax_rate_label=normalized_tax_rate_label,
                normalized_payload=payload_json,
                extraction_confidence=float(normalized["confidence"]),
            )
            self.db.add(extraction)
        return run, extraction

    @staticmethod
    def _to_date(value: object) -> date:
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            # Accept either YYYY-MM-DD or full ISO datetime strings.
            text = value.strip()
            if not text:
                return date.today()
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return date.today()
        return date.today()

    @classmethod
    def _to_jsonable(cls, value: object) -> object:
        if isinstance(value, dict):
            return {str(k): cls._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._to_jsonable(v) for v in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    async def _extract(self, file_path: str) -> tuple[dict, str]:
        guessed_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        file_name = file_path.split("/")[-1]

        # 1) Preferred: PaddleOCR HTTP service (no API key required).
        try:
            with open(file_path, "rb") as f:
                files = {
                    "file": (file_name, f, guessed_type),
                }
                async with httpx.AsyncClient(timeout=settings.paddle_ocr_timeout_seconds) as client:
                    resp = await client.post(settings.active_paddle_ocr_api_url, files=files)
                    resp.raise_for_status()
                    result = resp.json()
            return {
                "merchant_name": result.get("merchant_name") or result.get("merchant") or "Unknown Merchant",
                "transaction_date": result.get("transaction_date") or date.today().isoformat(),
                "registration_number": result.get("registration_number"),
                "registration_number_source": result.get("registration_number_source"),
                "registration_number_candidates": result.get("registration_number_candidates"),
                "amount_role_selected": result.get("amount_role_selected"),
                "amount_role_candidates": result.get("amount_role_candidates") or [],
                "subtotal_excl_tax": result.get("subtotal_excl_tax") or result.get("subtotal") or 0,
                "tax_amount": result.get("tax_amount") or 0,
                "total_amount": result.get("total_amount") or result.get("total") or 0,
                "tax_rate_label": str(result.get("tax_rate_label") or result.get("tax_rate") or "unknown"),
                "payment_method": result.get("payment_method"),
                "raw_text_original": result.get("raw_text_original", result.get("raw_text", "")),
                "raw_text_normalized": result.get("raw_text_normalized", result.get("raw_text", "")),
                "raw_text": result.get("raw_text", ""),
                "confidence": float(result.get("confidence", 0.8)),
            }, "paddle-ocr"
        except Exception as exc:
            logging.exception("Paddle OCR HTTP call failed for %s", file_path)
            # continue to fallback providers

        # 2) Optional fallback: receipt-ocr HTTP service.
        try:
            with open(file_path, "rb") as f:
                files = {
                    "file": (file_name, f, guessed_type),
                }
                async with httpx.AsyncClient(timeout=settings.receipt_ocr_timeout_seconds) as client:
                    resp = await client.post(settings.receipt_ocr_api_url, files=files)
                    resp.raise_for_status()
                    result = resp.json()

            return {
                "merchant_name": result.get("merchant_name") or result.get("merchant") or "Unknown Merchant",
                "transaction_date": result.get("transaction_date") or date.today().isoformat(),
                "registration_number": result.get("registration_number"),
                "registration_number_source": result.get("registration_number_source"),
                "registration_number_candidates": result.get("registration_number_candidates"),
                "amount_role_selected": result.get("amount_role_selected"),
                "amount_role_candidates": result.get("amount_role_candidates") or [],
                "subtotal_excl_tax": result.get("subtotal_excl_tax") or result.get("subtotal") or 0,
                "tax_amount": result.get("tax_amount") or 0,
                "total_amount": result.get("total_amount") or result.get("total") or 0,
                "tax_rate_label": str(result.get("tax_rate_label") or result.get("tax_rate") or "unknown"),
                "payment_method": result.get("payment_method"),
                "raw_text_original": result.get("raw_text_original", result.get("raw_text", "")),
                "raw_text_normalized": result.get("raw_text_normalized", result.get("raw_text", "")),
                "raw_text": result.get("raw_text", ""),
                "confidence": float(result.get("confidence", 0.8)),
            }, "receipt-ocr"
        except Exception as exc:
            logging.exception("Remote receipt-ocr HTTP call failed for %s", file_path)
            # continue to local fallback

        # 3) Local package fallback parser.
        try:
            from receipt_ocr import ReceiptProcessor  # type: ignore

            processor = ReceiptProcessor()
            result = processor.extract_receipt_data(file_path)
            return {
                "merchant_name": result.get("merchant_name") or result.get("merchant") or "Unknown Merchant",
                "transaction_date": result.get("transaction_date") or date.today().isoformat(),
                "registration_number": result.get("registration_number"),
                "registration_number_source": result.get("registration_number_source"),
                "registration_number_candidates": result.get("registration_number_candidates"),
                "amount_role_selected": result.get("amount_role_selected"),
                "amount_role_candidates": result.get("amount_role_candidates") or [],
                "subtotal_excl_tax": result.get("subtotal_excl_tax") or result.get("subtotal") or 0,
                "tax_amount": result.get("tax_amount") or 0,
                "total_amount": result.get("total_amount") or result.get("total") or 0,
                "tax_rate_label": str(result.get("tax_rate_label") or result.get("tax_rate") or "unknown"),
                "payment_method": result.get("payment_method"),
                "raw_text_original": result.get("raw_text_original", result.get("raw_text", "")),
                "raw_text_normalized": result.get("raw_text_normalized", result.get("raw_text", "")),
                "raw_text": result.get("raw_text", ""),
                "confidence": float(result.get("confidence", 0.8)),
            }, "local-receipt-ocr"
        except Exception as exc:
            # Log internal exception for operator debugging but do not
            # propagate provider implementation details (like missing
            # API keys) back to end users. Raise a generic failure so
            # callers receive a concise, non-sensitive error message.
            logging.exception("Local receipt_ocr processor failed for %s", file_path)

        # Final generic error raised when all providers failed.
        raise RuntimeError(f"All OCR providers failed for {file_path}")
