from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from ..database import Base, TimestampMixin


class AccountSubject(Base, TimestampMixin):
    __tablename__ = "account_subjects"
    __table_args__ = (
        UniqueConstraint("accounting_firm_id", "client_company_id", "subject_code", name="uq_subject_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=True)
    subject_code: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tax_treatment: Mapped[str] = mapped_column(String(30), nullable=False, default="deductible")
    default_tax_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ClassificationRule(Base, TimestampMixin):
    __tablename__ = "classification_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=True)
    industry_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False, default=100)
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    rule_condition: Mapped[dict] = mapped_column(JSON, nullable=False)
    target_subject_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("account_subjects.id"), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=1.0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ClassificationResult(Base):
    __tablename__ = "classification_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False)
    matched_rule_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("classification_rules.id"), nullable=True)
    subject_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("account_subjects.id"), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    decision_source: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ExportTemplate(Base, TimestampMixin):
    __tablename__ = "export_templates"
    __table_args__ = (
        UniqueConstraint("accounting_firm_id", "client_company_id", "template_code", name="uq_template_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=True)
    template_code: Mapped[str] = mapped_column(String(50), nullable=False)
    template_name: Mapped[str] = mapped_column(String(255), nullable=False)
    format_type: Mapped[str] = mapped_column(String(20), nullable=False, default="csv")
    delimiter: Mapped[str] = mapped_column(String(1), nullable=False, default=",")
    encoding: Mapped[str] = mapped_column(String(30), nullable=False, default="UTF-8")
    mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accounting_firm_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("accounting_firms.id"), nullable=False)
    client_company_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("client_companies.id"), nullable=False)
    template_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("export_templates.id"), nullable=False)
    requested_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    filter_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    job_status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    total_rows: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExportFile(Base):
    __tablename__ = "export_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    export_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("export_jobs.id"), nullable=False, unique=True
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
