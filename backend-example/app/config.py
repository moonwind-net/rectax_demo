from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/receipt_tax_system"
    database_pool_size: int = 10
    database_pool_recycle: int = 3600

    app_name: str = "JP Receipt Processing API"
    app_version: str = "1.0.0"
    debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # JWT
    jwt_secret_key: str = "CHANGE_THIS_SECRET_KEY_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7

    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "admin123456"
    bootstrap_admin_display_name: str = "Admin"
    bootstrap_admin_sync_existing: bool = True

    upload_dir: str = "./uploads"
    export_dir: str = "./exports"
    max_file_size: int = 10 * 1024 * 1024  # bytes, per-file hard limit

    # Upload guard limits (enforced server-side regardless of client)
    upload_rate_limit_per_minute: int = 10   # max upload requests per user per 60 s
    upload_max_files_per_request: int = 20   # max files in one multipart request
    upload_max_total_mb_per_request: int = 100  # max combined MB per request

    ocr_confidence_threshold: float = 0.65
    classification_confidence_threshold: float = 0.70
    paddle_ocr_target: str = "v3"
    paddle_ocr_api_url_v3: str = "http://paddle-ocr:8000/ocr/"
    paddle_ocr_api_url_v4: str = "http://paddle-ocr-v4:8000/ocr/"
    paddle_ocr_api_url: str = "http://paddle-ocr:8000/ocr/"
    paddle_ocr_timeout_seconds: int = 30
    receipt_ocr_api_url: str = "http://receipt-ocr:8000/ocr/"
    receipt_ocr_timeout_seconds: int = 30

    default_firm_id: int = 1
    default_company_id: int = 1
    default_user_id: int = 1

    # Folder watch scheduler interval (seconds)
    folder_watch_interval_seconds: int = 300

    # Async worker (DB task polling) settings
    worker_poll_interval_seconds: int = 3
    worker_batch_size: int = 5
    worker_concurrency: int = 2
    worker_max_attempts: int = 3
    worker_retry_base_seconds: int = 3
    worker_max_queue_depth: int = 2000

    jpy_rounding_mode: str = "floor"
    tax_rounding_level: str = "tax_rate"

    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    allowed_origin_regex: str = ""

    @property
    def active_paddle_ocr_api_url(self) -> str:
        target = self.paddle_ocr_target.strip().lower()
        if target == "v4":
            return self.paddle_ocr_api_url_v4
        if target == "v3":
            return self.paddle_ocr_api_url_v3
        # Backward-compatible: if target is unknown, respect explicit legacy URL.
        return self.paddle_ocr_api_url


settings = Settings()
