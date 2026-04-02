-- PostgreSQL init script for JP receipt processing system
-- Streamed-like workflow model (public feature abstraction)

BEGIN;

-- =========================
-- 1) Tenant and user domain
-- =========================

CREATE TABLE accounting_firms (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    country_code CHAR(2) NOT NULL DEFAULT 'JP',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE client_companies (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    code VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    legal_name VARCHAR(255),
    registration_number VARCHAR(14),
    industry_code VARCHAR(50),
    currency_code CHAR(3) NOT NULL DEFAULT 'JPY',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(accounting_firm_id, code)
);

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE memberships (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT REFERENCES client_companies(id),
    role VARCHAR(50) NOT NULL CHECK (role IN ('firm_admin', 'manager', 'operator', 'viewer')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, accounting_firm_id, client_company_id, role)
);

-- =========================
-- 2) Ingestion domain
-- =========================

CREATE TABLE ingestion_sources (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT NOT NULL REFERENCES client_companies(id),
    source_type VARCHAR(30) NOT NULL CHECK (source_type IN ('upload', 'local_folder', 'api')),
    source_name VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE local_folder_watches (
    id BIGSERIAL PRIMARY KEY,
    ingestion_source_id BIGINT NOT NULL UNIQUE REFERENCES ingestion_sources(id),
    folder_path TEXT NOT NULL,
    recursive BOOLEAN NOT NULL DEFAULT TRUE,
    include_pattern VARCHAR(255) DEFAULT '*',
    exclude_pattern VARCHAR(255),
    scan_interval_minutes INT NOT NULL DEFAULT 30,
    last_scanned_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE ingestion_jobs (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT NOT NULL REFERENCES client_companies(id),
    ingestion_source_id BIGINT NOT NULL REFERENCES ingestion_sources(id),
    requested_by BIGINT REFERENCES users(id),
    job_status VARCHAR(30) NOT NULL DEFAULT 'queued'
        CHECK (job_status IN ('queued', 'running', 'completed', 'failed', 'partial')),
    total_files INT NOT NULL DEFAULT 0,
    success_files INT NOT NULL DEFAULT 0,
    failed_files INT NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ingestion_jobs_company_status ON ingestion_jobs(client_company_id, job_status, created_at DESC);

CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT NOT NULL REFERENCES client_companies(id),
    ingestion_job_id BIGINT REFERENCES ingestion_jobs(id),
    ingestion_source_id BIGINT REFERENCES ingestion_sources(id),
    original_filename VARCHAR(255) NOT NULL,
    storage_path TEXT NOT NULL,
    file_ext VARCHAR(20),
    mime_type VARCHAR(100),
    file_size_bytes BIGINT,
    file_hash_sha256 CHAR(64),
    page_count INT,
    captured_at TIMESTAMP,
    document_status VARCHAR(30) NOT NULL DEFAULT 'uploaded'
        CHECK (document_status IN (
            'uploaded',
            'queued',
            'ocr_processing',
            'classifying',
            'ocr_done',
            'classified',
            'review_required',
            'approved',
            'rejected',
            'failed'
        )),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE INDEX idx_documents_company_status ON documents(client_company_id, document_status, created_at DESC);
CREATE UNIQUE INDEX idx_documents_hash_company ON documents(client_company_id, file_hash_sha256) WHERE file_hash_sha256 IS NOT NULL;

CREATE TABLE processing_tasks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id),
    task_status VARCHAR(30) NOT NULL DEFAULT 'queued'
        CHECK (task_status IN ('queued', 'processing', 'completed', 'failed')),
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    next_run_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_processing_tasks_status_next_run ON processing_tasks(task_status, next_run_at, id);

-- =========================
-- 3) OCR and extraction domain
-- =========================

CREATE TABLE ocr_runs (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    provider VARCHAR(50) NOT NULL,
    provider_model VARCHAR(100),
    run_status VARCHAR(30) NOT NULL DEFAULT 'queued'
        CHECK (run_status IN ('queued', 'running', 'completed', 'failed')),
    confidence_score NUMERIC(5,4),
    raw_text TEXT,
    raw_payload JSONB,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ocr_runs_document ON ocr_runs(document_id, created_at DESC);

CREATE TABLE receipt_extractions (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id),
    ocr_run_id BIGINT NOT NULL REFERENCES ocr_runs(id),
    transaction_date DATE,
    merchant_name VARCHAR(255),
    merchant_phone VARCHAR(40),
    merchant_address TEXT,
    registration_number VARCHAR(14),
    subtotal_excl_tax NUMERIC(14,2),
    tax_amount NUMERIC(14,2),
    total_amount NUMERIC(14,2),
    currency_code CHAR(3) NOT NULL DEFAULT 'JPY',
    payment_method VARCHAR(50),
    tax_rate_label VARCHAR(20) CHECK (tax_rate_label IN ('8', '10', 'mixed', 'unknown')) DEFAULT 'unknown',
    extracted_items JSONB,
    normalized_payload JSONB,
    extraction_confidence NUMERIC(5,4),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE receipt_tax_lines (
    id BIGSERIAL PRIMARY KEY,
    receipt_extraction_id BIGINT NOT NULL REFERENCES receipt_extractions(id),
    tax_rate NUMERIC(5,2) NOT NULL,
    taxable_amount NUMERIC(14,2),
    tax_amount NUMERIC(14,2),
    is_reduced_tax BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE document_flags (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    flag_code VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolved_by BIGINT REFERENCES users(id)
);

CREATE INDEX idx_document_flags_open ON document_flags(document_id, resolved_at) WHERE resolved_at IS NULL;

-- =========================
-- 4) Classification and accounting domain
-- =========================

CREATE TABLE account_subjects (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT REFERENCES client_companies(id),
    subject_code VARCHAR(50) NOT NULL,
    subject_name VARCHAR(255) NOT NULL,
    subject_type VARCHAR(20) NOT NULL CHECK (subject_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
    tax_treatment VARCHAR(30) NOT NULL DEFAULT 'deductible'
        CHECK (tax_treatment IN ('deductible', 'non_deductible', 'mixed')),
    default_tax_rate NUMERIC(5,2),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(accounting_firm_id, client_company_id, subject_code)
);

CREATE TABLE classification_rules (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT REFERENCES client_companies(id),
    industry_code VARCHAR(50),
    rule_name VARCHAR(255) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('keyword', 'merchant_exact', 'amount_range', 'regex')),
    rule_condition JSONB NOT NULL,
    target_subject_id BIGINT NOT NULL REFERENCES account_subjects(id),
    score NUMERIC(5,4) NOT NULL DEFAULT 1.0000,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_classification_rules_scope ON classification_rules(accounting_firm_id, client_company_id, industry_code, is_active);

CREATE TABLE classification_results (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id),
    matched_rule_id BIGINT REFERENCES classification_rules(id),
    subject_id BIGINT REFERENCES account_subjects(id),
    confidence_score NUMERIC(5,4),
    decision_source VARCHAR(30) NOT NULL CHECK (decision_source IN ('auto', 'manual')),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE review_tasks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL UNIQUE REFERENCES documents(id),
    task_status VARCHAR(30) NOT NULL DEFAULT 'pending'
        CHECK (task_status IN ('pending', 'in_progress', 'resolved', 'rejected')),
    reason_codes TEXT[] NOT NULL,
    assigned_to BIGINT REFERENCES users(id),
    resolved_by BIGINT REFERENCES users(id),
    resolution_note TEXT,
    resolved_subject_id BIGINT REFERENCES account_subjects(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

CREATE INDEX idx_review_tasks_status ON review_tasks(task_status, created_at DESC);

-- Review audit logs (per-review task changes and document linkage)
CREATE TABLE review_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    review_task_id BIGINT NOT NULL REFERENCES review_tasks(id),
    document_id BIGINT NOT NULL REFERENCES documents(id),
    action_type VARCHAR(30) NOT NULL,
    changed_by BIGINT REFERENCES users(id),
    reason_note TEXT,
    before_json JSONB,
    after_json JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_review_audit_logs_document ON review_audit_logs(document_id, created_at DESC);

-- Correction templates used for review/autofill
CREATE TABLE correction_templates (
    id BIGSERIAL PRIMARY KEY,
    template_key VARCHAR(100) NOT NULL UNIQUE,
    label VARCHAR(255) NOT NULL,
    merchant_pattern VARCHAR(255),
    patch_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    note_prefix VARCHAR(255),
    priority INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =========================
-- 5) Export domain
-- =========================

CREATE TABLE export_templates (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT REFERENCES client_companies(id),
    template_code VARCHAR(50) NOT NULL,
    template_name VARCHAR(255) NOT NULL,
    format_type VARCHAR(20) NOT NULL CHECK (format_type IN ('csv')),
    delimiter CHAR(1) NOT NULL DEFAULT ',',
    encoding VARCHAR(30) NOT NULL DEFAULT 'UTF-8',
    mapping_json JSONB NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(accounting_firm_id, client_company_id, template_code)
);

CREATE TABLE export_jobs (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT NOT NULL REFERENCES client_companies(id),
    template_id BIGINT NOT NULL REFERENCES export_templates(id),
    requested_by BIGINT REFERENCES users(id),
    filter_json JSONB,
    job_status VARCHAR(30) NOT NULL DEFAULT 'queued'
        CHECK (job_status IN ('queued', 'running', 'completed', 'failed')),
    total_rows INT,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE export_files (
    id BIGSERIAL PRIMARY KEY,
    export_job_id BIGINT NOT NULL UNIQUE REFERENCES export_jobs(id),
    file_path TEXT NOT NULL,
    file_size_bytes BIGINT,
    checksum_sha256 CHAR(64),
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =========================
-- 6) Audit domain
-- =========================

CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    accounting_firm_id BIGINT NOT NULL REFERENCES accounting_firms(id),
    client_company_id BIGINT REFERENCES client_companies(id),
    user_id BIGINT REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id BIGINT,
    changes JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_audit_logs_scope_time ON audit_logs(accounting_firm_id, client_company_id, created_at DESC);

-- =========================
-- 7) Seed data
-- =========================

INSERT INTO accounting_firms (code, name) VALUES
('FIRM001', 'A税理士事務所');

INSERT INTO client_companies (accounting_firm_id, code, name, legal_name, registration_number, industry_code) VALUES
(1, 'C001', '株式会社サンプル食品', '株式会社サンプル食品', 'T1234567890123', 'food'),
(1, 'C002', '株式会社サンプルIT', '株式会社サンプルIT', 'T2345678901234', 'it');

INSERT INTO users (email, display_name, password_hash) VALUES
('admin@example.com', 'Firm Admin', '$2b$12$placeholder'),
('operator@example.com', 'Operator', '$2b$12$placeholder');

INSERT INTO memberships (user_id, accounting_firm_id, client_company_id, role) VALUES
(1, 1, NULL, 'firm_admin'),
(2, 1, 1, 'operator');

INSERT INTO ingestion_sources (accounting_firm_id, client_company_id, source_type, source_name, created_by) VALUES
(1, 1, 'upload', 'Manual Upload', 2),
(1, 1, 'local_folder', 'Accounting Shared Folder', 1);

INSERT INTO local_folder_watches (ingestion_source_id, folder_path, recursive, include_pattern, scan_interval_minutes) VALUES
(2, 'D:/Accounting/Receipts/C001', TRUE, '*', 30);

INSERT INTO account_subjects (accounting_firm_id, client_company_id, subject_code, subject_name, subject_type, tax_treatment, default_tax_rate) VALUES
(1, 1, '6110', '消耗品費', 'expense', 'deductible', 10.00),
(1, 1, '6120', '事務用品費', 'expense', 'deductible', 10.00),
(1, 1, '6130', '新聞図書費', 'expense', 'deductible', 10.00),
(1, 1, '6140', '通信費', 'expense', 'deductible', 10.00),
(1, 1, '6150', '水道光熱費', 'expense', 'deductible', 10.00),
(1, 1, '6160', '地代家賃', 'expense', 'deductible', 10.00),
(1, 1, '6170', '修繕費', 'expense', 'deductible', 10.00),
(1, 1, '6180', '車両費', 'expense', 'deductible', 10.00),
(1, 1, '6190', '荷造運賃', 'expense', 'deductible', 10.00),
(1, 1, '6200', '支払手数料', 'expense', 'deductible', 10.00),
(1, 1, '6210', '旅費交通費', 'expense', 'deductible', 10.00),
(1, 1, '6220', '会議費', 'expense', 'deductible', 10.00),
(1, 1, '6230', '接待交際費', 'expense', 'mixed', 10.00),
(1, 1, '6240', '広告宣伝費', 'expense', 'deductible', 10.00),
(1, 1, '6250', '福利厚生費', 'expense', 'deductible', 10.00),
(1, 1, '6260', '外注費', 'expense', 'deductible', 10.00),
(1, 1, '6270', '支払報酬料', 'expense', 'deductible', 10.00),
(1, 1, '6280', '研修費', 'expense', 'deductible', 10.00),
(1, 1, '6290', '保険料', 'expense', 'mixed', NULL),
(1, 1, '6300', '租税公課', 'expense', 'non_deductible', NULL),
(1, 1, '6310', '雑費', 'expense', 'deductible', 10.00),
(1, 1, '6400', '仕入高(標準税率)', 'expense', 'deductible', 10.00),
(1, 1, '6410', '仕入高(軽減税率)', 'expense', 'deductible', 8.00);

INSERT INTO export_templates (accounting_firm_id, client_company_id, template_code, template_name, format_type, mapping_json, is_default) VALUES
(
    1,
    1,
    'JP_STD_A',
    '日本会計CSV標準A',
    'csv',
    '{
      "columns": [
        {"header": "取引日", "field": "transaction_date", "format": "yyyy-mm-dd"},
        {"header": "取引先", "field": "merchant_name"},
        {"header": "登録番号", "field": "registration_number"},
        {"header": "勘定科目", "field": "subject_name"},
        {"header": "税率", "field": "tax_rate_label"},
        {"header": "税額", "field": "tax_amount"},
        {"header": "金額", "field": "total_amount"}
      ]
    }'::jsonb,
    TRUE
);

COMMIT;