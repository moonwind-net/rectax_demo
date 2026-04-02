import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, text

from .config import settings
from .database import AsyncSessionLocal, Base, engine
from .models.user import AccountingFirm, ClientCompany, Membership, User
from .routers import auth, categories, company_settings, correction_templates, documents, exports, ingestion, review
from .services.auth_service import _hash_password

# Ensure model metadata is loaded before create_all.
from . import models  # noqa: F401

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


async def _ensure_bootstrap_admin() -> None:
    """Create or synchronize bootstrap admin credentials."""
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.email == settings.bootstrap_admin_email))
        ).scalars().first()
        if existing:
            if settings.bootstrap_admin_sync_existing:
                existing.display_name = settings.bootstrap_admin_display_name
                existing.password_hash = _hash_password(settings.bootstrap_admin_password)
                existing.is_active = True
                await db.commit()
                logger.info("Bootstrap admin user synchronized: %s", settings.bootstrap_admin_email)
            return

        user = User(
            email=settings.bootstrap_admin_email,
            display_name=settings.bootstrap_admin_display_name,
            password_hash=_hash_password(settings.bootstrap_admin_password),
            is_active=True,
        )
        db.add(user)
        await db.commit()
        logger.info("Bootstrap admin user created: %s", settings.bootstrap_admin_email)


async def _ensure_default_org_data() -> None:
    async with AsyncSessionLocal() as db:
        firm = await db.get(AccountingFirm, settings.default_firm_id)
        if not firm:
            firm = AccountingFirm(id=settings.default_firm_id, code="DEFAULT", name="Default Firm", country_code="JP")
            db.add(firm)
            await db.flush()

        company = await db.get(ClientCompany, settings.default_company_id)
        if not company:
            company = ClientCompany(
                id=settings.default_company_id,
                accounting_firm_id=settings.default_firm_id,
                code="DEFAULT",
                name="Default Company",
                currency_code="JPY",
                is_active=True,
            )
            db.add(company)
            await db.flush()

        admin = (
            await db.execute(select(User).where(User.email == settings.bootstrap_admin_email))
        ).scalars().first()
        if admin:
            membership = (
                await db.execute(
                    select(Membership)
                    .where(Membership.user_id == admin.id)
                    .where(Membership.accounting_firm_id == settings.default_firm_id)
                    .where(Membership.client_company_id == settings.default_company_id)
                    .where(Membership.role == "firm_admin")
                )
            ).scalars().first()
            if not membership:
                db.add(
                    Membership(
                        user_id=admin.id,
                        accounting_firm_id=settings.default_firm_id,
                        client_company_id=settings.default_company_id,
                        role="firm_admin",
                    )
                )
        await db.commit()


async def _ensure_document_status_constraint() -> None:
    """Ensure DB CHECK constraint includes async pipeline statuses.

    This keeps existing databases compatible without introducing an external
    migration framework in this minimal async version.
    """
    drop_sql = "ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_document_status_check"
    add_sql = """
    ALTER TABLE documents
    ADD CONSTRAINT documents_document_status_check
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
    ))
    """
    async with AsyncSessionLocal() as db:
        await db.execute(text(drop_sql))
        await db.execute(text(add_sql))
        await db.commit()


async def _ensure_company_tax_rule_columns() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("ALTER TABLE client_companies ADD COLUMN IF NOT EXISTS jpy_rounding_mode VARCHAR(20)"))
        await db.execute(text("ALTER TABLE client_companies ADD COLUMN IF NOT EXISTS tax_rounding_level VARCHAR(20)"))
        await db.commit()


async def _scheduled_folder_scan() -> None:
    """Periodic task: scan all active local folder watch sources."""
    from .services.ingestion_service import IngestionService

    async with AsyncSessionLocal() as db:
        service = IngestionService(db)
        try:
            sources = await service.list_folder_sources()
            for s in sources:
                if s.get("is_active") and s.get("folder_path"):
                    try:
                        job_id, doc_ids = await service.scan_folder_watch(s["id"])
                        if doc_ids:
                            logger.info("Folder scan job=%s ingested %d documents", job_id, len(doc_ids))
                    except Exception as exc:
                        logger.warning("Folder scan failed for source %s: %s", s["id"], exc)
        except Exception as exc:
            logger.error("Scheduled folder scan error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure structured logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _ensure_company_tax_rule_columns()
    await _ensure_document_status_constraint()

    await _ensure_bootstrap_admin()
    await _ensure_default_org_data()

    # Start folder-watch scheduler
    _scheduler.add_job(
        _scheduled_folder_scan,
        "interval",
        seconds=settings.folder_watch_interval_seconds,
        id="folder_scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started (folder_watch_interval=%ss)", settings.folder_watch_interval_seconds)

    yield

    _scheduler.shutdown(wait=False)
    await engine.dispose()


# 创建FastAPI应用
app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=settings.allowed_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject requests from unexpected Host headers (prevents Host header injection)
if not settings.debug:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


api_v1_prefix = "/api/v1"

app.include_router(auth.router, prefix=f"{api_v1_prefix}/auth", tags=["auth"])
app.include_router(ingestion.router, prefix=f"{api_v1_prefix}/ingestion", tags=["ingestion"])
app.include_router(documents.router, prefix=f"{api_v1_prefix}/documents", tags=["documents"])
app.include_router(review.router, prefix=f"{api_v1_prefix}/review", tags=["review"])
app.include_router(categories.router, prefix=f"{api_v1_prefix}/categories", tags=["categories"])
app.include_router(exports.router, prefix=f"{api_v1_prefix}/exports", tags=["exports"])
app.include_router(correction_templates.router, prefix=f"{api_v1_prefix}/correction-templates", tags=["correction-templates"])
app.include_router(company_settings.router, prefix=f"{api_v1_prefix}/company-settings", tags=["company-settings"])


@app.get("/health")
async def health_check():
    """Liveness + DB connectivity probe."""
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("Health check DB ping failed: %s", exc)
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "service": settings.app_name,
        "version": settings.app_version,
        "ocr_target": settings.paddle_ocr_target,
        "ocr_endpoint": settings.active_paddle_ocr_api_url,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )
