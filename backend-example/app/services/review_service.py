from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.category import ClassificationResult
from ..models.receipt import Document, ReceiptExtraction, ReceiptTaxLine, ReviewAuditLog, ReviewTask
from ..models.user import ClientCompany
from .tax_rules import get_company_tax_config, inclusive_tax_from_total, normalize_tax_lines, round_to_yen


class ReviewService:
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

    async def list_pending(self, company_id: int | None = None) -> list[ReviewTask]:
        stmt = (
            select(ReviewTask)
            .join(Document, Document.id == ReviewTask.document_id)
            .where(ReviewTask.task_status.in_(["pending", "in_progress"]))
            .order_by(ReviewTask.id.asc())
        )
        if company_id is not None:
            stmt = stmt.where(Document.client_company_id == company_id)
        return (await self.db.execute(stmt)).scalars().all()

    @staticmethod
    def _snapshot_extraction(extraction: ReceiptExtraction | None) -> dict:
        if not extraction:
            return {}
        return {
            "merchant_name": extraction.merchant_name,
            "transaction_date": str(extraction.transaction_date) if extraction.transaction_date else None,
            "registration_number": extraction.registration_number,
            "tax_amount": float(extraction.tax_amount) if extraction.tax_amount is not None else None,
            "total_amount": float(extraction.total_amount) if extraction.total_amount is not None else None,
            "tax_rate_label": extraction.tax_rate_label,
            "confidence": float(extraction.extraction_confidence) if extraction.extraction_confidence is not None else None,
        }

    @staticmethod
    def _snapshot_tax_lines(tax_lines: list[ReceiptTaxLine]) -> list[dict]:
        return [
            {
                "tax_rate": float(line.tax_rate),
                "taxable_amount": float(line.taxable_amount) if line.taxable_amount is not None else None,
                "tax_amount": float(line.tax_amount) if line.tax_amount is not None else None,
                "is_reduced_tax": line.is_reduced_tax,
            }
            for line in tax_lines
        ]

    @staticmethod
    def _apply_extraction_patch(extraction: ReceiptExtraction, patch: dict | None) -> None:
        if not patch:
            return

        allowed_keys = {
            "merchant_name",
            "transaction_date",
            "registration_number",
            "tax_amount",
            "total_amount",
            "tax_rate_label",
        }

        for key, value in patch.items():
            if key not in allowed_keys:
                continue

            if key == "transaction_date":
                if value in (None, ""):
                    extraction.transaction_date = None
                else:
                    extraction.transaction_date = date.fromisoformat(str(value))
                continue

            if key in {"tax_amount", "total_amount"}:
                setattr(extraction, key, None if value in (None, "") else float(value))
                continue

            if key == "tax_rate_label":
                extraction.tax_rate_label = ReviewService._normalize_tax_rate_label(value)
                continue

            setattr(extraction, key, None if value == "" else value)

    async def _replace_tax_lines(
        self,
        extraction: ReceiptExtraction,
        tax_lines_payload: list[dict] | None,
        rounding_mode: str,
        rounding_level: str,
    ) -> list[ReceiptTaxLine]:
        existing_tax_lines = (
            await self.db.execute(
                select(ReceiptTaxLine)
                .where(ReceiptTaxLine.receipt_extraction_id == extraction.id)
                .order_by(ReceiptTaxLine.id.asc())
            )
        ).scalars().all()

        if tax_lines_payload is None:
            return existing_tax_lines

        for line in existing_tax_lines:
            await self.db.delete(line)
        await self.db.flush()

        normalized_lines, _ = normalize_tax_lines(
            tax_lines_payload,
            rounding_mode,
            rounding_level,
        )

        new_tax_lines: list[ReceiptTaxLine] = []
        for raw_line in normalized_lines:
            line = ReceiptTaxLine(
                receipt_extraction_id=extraction.id,
                tax_rate=float(raw_line["tax_rate"]),
                taxable_amount=None if raw_line["taxable_amount"] in (None, "") else float(raw_line["taxable_amount"]),
                tax_amount=None if raw_line["tax_amount"] in (None, "") else float(raw_line["tax_amount"]),
                is_reduced_tax=bool(raw_line.get("is_reduced_tax", False)),
            )
            self.db.add(line)
            new_tax_lines.append(line)
        await self.db.flush()
        return new_tax_lines

    async def resolve(
        self,
        task_id: int,
        action: str,
        subject_id: int | None,
        note: str | None,
        corrected_extraction: dict | None = None,
        corrected_tax_lines: list[dict] | None = None,
        use_recommended_subject: bool = False,
    ) -> ReviewTask:
        task = await self.db.get(ReviewTask, task_id)
        if not task:
            raise ValueError("Review task not found")

        document = await self.db.get(Document, task.document_id)
        if not document:
            raise ValueError("Document not found")
        company = await self.db.get(ClientCompany, document.client_company_id)
        company_tax_config = get_company_tax_config(company)

        extraction = (
            await self.db.execute(select(ReceiptExtraction).where(ReceiptExtraction.document_id == task.document_id))
        ).scalars().first()
        latest_auto_result = (
            await self.db.execute(
                select(ClassificationResult)
                .where(ClassificationResult.document_id == task.document_id)
                .order_by(ClassificationResult.id.desc())
                .limit(1)
            )
        ).scalars().first()
        tax_lines = []
        if extraction:
            tax_lines = (
                await self.db.execute(
                    select(ReceiptTaxLine)
                    .where(ReceiptTaxLine.receipt_extraction_id == extraction.id)
                    .order_by(ReceiptTaxLine.id.asc())
                )
            ).scalars().all()

        before_snapshot = {
            "document_status": document.document_status,
            "task_status": task.task_status,
            "resolved_subject_id": task.resolved_subject_id,
            "extraction": self._snapshot_extraction(extraction),
            "tax_lines": self._snapshot_tax_lines(tax_lines),
        }

        if extraction and corrected_extraction:
            self._apply_extraction_patch(extraction, corrected_extraction)
            if extraction.total_amount is not None:
                extraction.total_amount = round_to_yen(extraction.total_amount, company_tax_config.jpy_rounding_mode)
        if extraction:
            tax_lines = await self._replace_tax_lines(
                extraction,
                corrected_tax_lines,
                company_tax_config.jpy_rounding_mode,
                company_tax_config.tax_rounding_level,
            )
            normalized_rate_label = self._normalize_tax_rate_label(extraction.tax_rate_label)
            extraction.tax_rate_label = normalized_rate_label
            if normalized_rate_label == "mixed":
                extraction.tax_amount = sum(float(line.tax_amount or 0) for line in tax_lines)
            else:
                rate_percent = None
                if normalized_rate_label == "8":
                    rate_percent = 8
                elif normalized_rate_label == "10":
                    rate_percent = 10
                if rate_percent is not None and extraction.total_amount is not None:
                    extraction.tax_amount = inclusive_tax_from_total(
                        extraction.total_amount,
                        rate_percent,
                        company_tax_config.jpy_rounding_mode,
                    )
                elif extraction.tax_amount is not None:
                    extraction.tax_amount = round_to_yen(extraction.tax_amount, company_tax_config.jpy_rounding_mode)

        final_subject_id = subject_id
        if final_subject_id is None and use_recommended_subject and latest_auto_result and latest_auto_result.subject_id:
            final_subject_id = latest_auto_result.subject_id

        if action == "approve":
            if extraction is None:
                raise ValueError("Extraction not found")

            missing_fields: list[str] = []
            if not extraction.merchant_name:
                missing_fields.append("merchant_name")
            if extraction.total_amount is None:
                missing_fields.append("total_amount")
            if not extraction.tax_rate_label or self._normalize_tax_rate_label(extraction.tax_rate_label) == "unknown":
                missing_fields.append("tax_rate_label")
            if final_subject_id is None:
                missing_fields.append("subject_id")

            if missing_fields:
                raise ValueError(f"必須項目が不足しています: {', '.join(missing_fields)}")

            task.task_status = "resolved"
            task.resolved_subject_id = final_subject_id
            task.resolution_note = note
            task.resolved_by = settings.default_user_id
            task.resolved_at = datetime.utcnow()
            document.document_status = "approved"

            if final_subject_id:
                self.db.add(
                    ClassificationResult(
                        document_id=document.id,
                        matched_rule_id=None,
                        subject_id=final_subject_id,
                        confidence_score=1.0,
                        decision_source="manual",
                        created_by=settings.default_user_id,
                    )
                )
        else:
            # Correction-first flow: send back to in-progress review rather than terminal reject.
            task.task_status = "in_progress"
            task.resolution_note = note
            task.resolved_by = None
            task.resolved_at = None
            document.document_status = "review_required"

        after_snapshot = {
            "document_status": document.document_status,
            "task_status": task.task_status,
            "resolved_subject_id": task.resolved_subject_id,
            "extraction": self._snapshot_extraction(extraction),
            "tax_lines": self._snapshot_tax_lines(tax_lines),
        }

        self.db.add(
            ReviewAuditLog(
                review_task_id=task.id,
                document_id=document.id,
                action_type="approve" if action == "approve" else "request_correction",
                changed_by=settings.default_user_id,
                reason_note=note,
                before_json=before_snapshot,
                after_json=after_snapshot,
            )
        )

        await self.db.commit()
        await self.db.refresh(task)
        return task
