from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Date,
    ForeignKey,
    Numeric,
    DateTime,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from ..database import Base, TimestampMixin


class IngestionSource(Base, TimestampMixin):
    __tablename__ = "ingestion_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)


class LocalFolderWatch(Base, TimestampMixin):
    __tablename__ = "local_folder_watches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingestion_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ingestion_sources.id"), nullable=False, unique=True
    )
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    recursive: Mapped[bool] = mapped_column(default=True, nullable=False)
    include_pattern: Mapped[str] = mapped_column(String(255), default="*", nullable=False)
    exclude_pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scan_interval_minutes: Mapped[int] = mapped_column(default=30, nullable=False)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=False)
    ingestion_source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ingestion_sources.id"), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    job_status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    total_files: Mapped[int] = mapped_column(nullable=False, default=0)
    success_files: Mapped[int] = mapped_column(nullable=False, default=0)
    failed_files: Mapped[int] = mapped_column(nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProcessingTask(Base, TimestampMixin):
    __tablename__ = "processing_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False, unique=True)
    task_status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=False)
    ingestion_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ingestion_jobs.id"), nullable=True)
    ingestion_source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_sources.id"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_ext: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_hash_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_status: Mapped[str] = mapped_column(String(30), nullable=False, default="uploaded")


class OcrRun(Base):
    __tablename__ = "ocr_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    run_status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReceiptExtraction(Base, TimestampMixin):
    __tablename__ = "receipt_extractions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False, unique=True)
    ocr_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ocr_runs.id"), nullable=False)
    transaction_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    merchant_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_number: Mapped[str | None] = mapped_column(String(14), nullable=True)
    subtotal_excl_tax: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="JPY")
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_rate_label: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    extracted_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    normalized_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)


class ReceiptTaxLine(Base):
    __tablename__ = "receipt_tax_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    receipt_extraction_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("receipt_extractions.id"), nullable=False
    )
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    taxable_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    is_reduced_tax: Mapped[bool] = mapped_column(default=False, nullable=False)


class DocumentFlag(Base):
    __tablename__ = "document_flags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False)
    flag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)


class ReviewTask(Base, TimestampMixin):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False, unique=True)
    task_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_subject_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("account_subjects.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReviewAuditLog(Base):
    __tablename__ = "review_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    review_task_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("review_tasks.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CorrectionTemplate(Base, TimestampMixin):
    __tablename__ = "correction_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    template_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    patch_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note_prefix: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
