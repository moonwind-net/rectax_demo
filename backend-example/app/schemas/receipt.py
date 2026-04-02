from pydantic import BaseModel, Field


class IngestionUploadResponse(BaseModel):
    ingestion_job_id: int
    document_ids: list[int]
    duplicate_document_ids: list[int] = Field(default_factory=list)
    status: str


class DocumentListItem(BaseModel):
    id: int
    original_filename: str
    document_status: str
    merchant_name: str | None = None
    total_amount: float | None = None
    confidence: float | None = None
    stage_started_at: str | None = None
    stage_finished_at: str | None = None
    stage_duration_seconds: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    retry_attempts: int | None = None
    failure_reason: str | None = None
    can_retry: bool = False
    uploader_id: int | None = None
    uploader_name: str | None = None
    uploader_email: str | None = None


class DocumentDetailResponse(BaseModel):
    id: int
    original_filename: str
    storage_path: str
    document_status: str
    extraction: dict | None = None
    classification: dict | None = None
    review_task: dict | None = None


class ReviewResolveRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    subject_id: int | None = None
    note: str | None = None
    corrected_extraction: dict | None = None
    corrected_tax_lines: list[dict] | None = None
    use_recommended_subject: bool = False


class CorrectionTemplateCreate(BaseModel):
    template_key: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_\-]+$')
    label: str = Field(min_length=1, max_length=255)
    merchant_pattern: str | None = Field(default=None, max_length=255)
    patch_fields: dict = Field(default_factory=dict)
    note_prefix: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True


class CorrectionTemplateUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    merchant_pattern: str | None = Field(default=None, max_length=255)
    patch_fields: dict | None = None
    note_prefix: str | None = Field(default=None, max_length=255)
    priority: int | None = Field(default=None, ge=0, le=9999)
    is_active: bool | None = None


class CorrectionTemplateResponse(BaseModel):
    id: int
    template_key: str
    label: str
    merchant_pattern: str | None
    patch_fields: dict
    note_prefix: str | None
    priority: int
    is_active: bool
    matched: bool = False

    class Config:
        from_attributes = True


class CompanyTaxRuleResponse(BaseModel):
    id: int
    accounting_firm_id: int
    code: str
    name: str
    registration_number: str | None = None
    is_active: bool
    jpy_rounding_mode: str
    tax_rounding_level: str


class CompanyTaxRuleUpdate(BaseModel):
    jpy_rounding_mode: str = Field(pattern="^(floor|round|ceil)$")
    tax_rounding_level: str = Field(pattern="^(document|tax_rate|line)$")


class ExportCreateRequest(BaseModel):
    template_id: int
    only_approved: bool = True


class ExportCreateResponse(BaseModel):
    export_job_id: int
    export_file_id: int
    file_path: str
