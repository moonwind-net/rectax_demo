from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import CurrentCompanyContext, get_current_company, get_current_user
from ..models.user import User
from ..schemas.receipt import IngestionUploadResponse
from ..services.ingestion_service import IngestionService
from ..services.rate_limit import require_upload_rate_ok

router = APIRouter()


@router.post("/upload", response_model=IngestionUploadResponse)
async def upload_receipts(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
    _rate_ok: None = Depends(require_upload_rate_ok),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # ── Guard 1: file count ────────────────────────────────────────────
    if len(files) > settings.upload_max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=(
                f"一度に送信できるファイルは最大 {settings.upload_max_files_per_request} 件です。"
                f" ({len(files)} 件受信)"
            ),
        )

    # ── Guard 2: total declared size (multipart Content-Length per part) ─
    # file.size is populated by starlette from the part's Content-Length header.
    # A malicious client could omit the header (size == None), so this is an
    # early-rejection fast-path only; the hard limit is enforced when bytes are
    # actually read in upload_and_enqueue.
    max_total_bytes = settings.upload_max_total_mb_per_request * 1024 * 1024
    declared_total = sum(f.size for f in files if f.size is not None)
    if declared_total > max_total_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"合計ファイルサイズが上限 {settings.upload_max_total_mb_per_request} MB を超えています。"
            ),
        )

    service = IngestionService(db, current_company.accounting_firm_id, current_company.company_id)
    backlog = await service.get_processing_backlog()
    if backlog >= settings.worker_max_queue_depth:
        raise HTTPException(
            status_code=503,
            detail=(
                "現在処理キューが混雑しています。しばらく待ってから再試行してください。"
                f" (queue_depth={backlog})"
            ),
        )

    try:
        job_id, document_ids, duplicate_document_ids = await service.upload_and_enqueue(
            files,
            requested_by_user_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return IngestionUploadResponse(
        ingestion_job_id=job_id,
        document_ids=document_ids,
        duplicate_document_ids=duplicate_document_ids,
        status="accepted",
    )


@router.get("/folder-sources")
async def list_folder_sources(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    """List all configured local folder watch sources."""
    service = IngestionService(db, current_company.accounting_firm_id, current_company.company_id)
    return await service.list_folder_sources()


@router.post("/folder-sources/{source_id}/scan", response_model=IngestionUploadResponse)
async def scan_folder_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    """Trigger an immediate scan of the given local folder watch source."""
    service = IngestionService(db, current_company.accounting_firm_id, current_company.company_id)
    try:
        job_id, document_ids = await service.scan_folder_watch(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IngestionUploadResponse(ingestion_job_id=job_id, document_ids=document_ids, status="completed")
