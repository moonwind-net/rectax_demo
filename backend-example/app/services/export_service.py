import csv
import hashlib
import os
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.category import ClassificationResult, ExportFile, ExportJob, ExportTemplate
from ..models.receipt import Document, ReceiptExtraction
from ..models.user import ClientCompany
from .tax_rules import get_company_tax_config, round_to_yen


class ExportService:
    def __init__(self, db: AsyncSession, accounting_firm_id: int | None = None, client_company_id: int | None = None):
        self.db = db
        self.accounting_firm_id = accounting_firm_id or settings.default_firm_id
        self.client_company_id = client_company_id or settings.default_company_id

    async def create_export(self, template_id: int, only_approved: bool = True) -> tuple[ExportJob, ExportFile]:
        template = await self.db.get(ExportTemplate, template_id)
        if not template:
            raise ValueError("Template not found")

        export_job = ExportJob(
            accounting_firm_id=self.accounting_firm_id,
            client_company_id=self.client_company_id,
            template_id=template_id,
            requested_by=settings.default_user_id,
            filter_json={"only_approved": only_approved},
            job_status="running",
        )
        self.db.add(export_job)
        await self.db.flush()

        rows = await self._build_rows(only_approved)
        csv_path = self._write_csv(template.mapping_json, rows)

        export_job.total_rows = len(rows)
        export_job.job_status = "completed"

        export_file = ExportFile(
            export_job_id=export_job.id,
            file_path=csv_path,
            file_size_bytes=os.path.getsize(csv_path),
            checksum_sha256=self._checksum(csv_path),
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        self.db.add(export_file)
        await self.db.commit()
        await self.db.refresh(export_job)
        await self.db.refresh(export_file)

        return export_job, export_file

    async def _build_rows(self, only_approved: bool) -> list[dict]:
        stmt = (
            select(Document, ReceiptExtraction, ClassificationResult)
            .join(ReceiptExtraction, ReceiptExtraction.document_id == Document.id)
            .outerjoin(ClassificationResult, ClassificationResult.document_id == Document.id)
            .where(Document.client_company_id == self.client_company_id)
        )
        if only_approved:
            stmt = stmt.where(Document.document_status == "approved")

        rows = []
        data = (await self.db.execute(stmt)).all()
        for document, extraction, classification in data:
            company = await self.db.get(ClientCompany, document.client_company_id)
            company_tax_config = get_company_tax_config(company)
            rows.append(
                {
                    "document_id": document.id,
                    "transaction_date": str(extraction.transaction_date) if extraction.transaction_date else "",
                    "merchant_name": extraction.merchant_name or "",
                    "registration_number": extraction.registration_number or "",
                    "tax_rate_label": extraction.tax_rate_label,
                    "tax_amount": round_to_yen(extraction.tax_amount or 0, company_tax_config.jpy_rounding_mode) or 0,
                    "total_amount": round_to_yen(extraction.total_amount or 0, company_tax_config.jpy_rounding_mode) or 0,
                    "subject_id": classification.subject_id if classification else "",
                }
            )
        return rows

    def _write_csv(self, mapping_json: dict, rows: list[dict]) -> str:
        os.makedirs(settings.export_dir, exist_ok=True)
        path = os.path.join(settings.export_dir, f"export_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv")

        columns = mapping_json.get("columns", [])
        headers = [c.get("header", c.get("field")) for c in columns]
        fields = [c.get("field") for c in columns]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([row.get(field, "") for field in fields])

        return path

    @staticmethod
    def _checksum(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
