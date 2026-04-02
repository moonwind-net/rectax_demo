from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import CurrentCompanyContext, get_current_company, get_current_user
from ..models.category import ClassificationResult
from ..models.receipt import Document, IngestionJob, ProcessingTask, ReceiptExtraction, ReviewTask
from ..models.user import User
from ..schemas.receipt import DocumentDetailResponse, DocumentListItem

router = APIRouter()


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _duration_seconds(started: datetime | None, finished: datetime | None) -> int | None:
    if started is None:
        return None
    end = finished or datetime.utcnow()
    return max(0, int((end - started).total_seconds()))


@router.get("", response_model=list[DocumentListItem])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    stmt = (
        select(Document, ReceiptExtraction, ProcessingTask, IngestionJob, User)
        .outerjoin(ReceiptExtraction, ReceiptExtraction.document_id == Document.id)
        .outerjoin(ProcessingTask, ProcessingTask.document_id == Document.id)
        .outerjoin(IngestionJob, IngestionJob.id == Document.ingestion_job_id)
        .outerjoin(User, User.id == IngestionJob.requested_by)
        .where(Document.client_company_id == current_company.company_id)
    )
    rows = (await db.execute(stmt)).all()
    return [
        DocumentListItem(
            id=document.id,
            original_filename=document.original_filename,
            document_status=document.document_status,
            merchant_name=extraction.merchant_name if extraction else None,
            total_amount=float(extraction.total_amount) if extraction and extraction.total_amount is not None else None,
            confidence=float(extraction.extraction_confidence) if extraction and extraction.extraction_confidence else None,
            stage_started_at=_to_iso(task.started_at) if task else None,
            stage_finished_at=_to_iso(task.finished_at) if task else None,
            stage_duration_seconds=_duration_seconds(task.started_at, task.finished_at) if task else None,
            created_at=_to_iso(document.created_at),
            updated_at=_to_iso(document.updated_at),
            retry_attempts=task.attempts if task else None,
            failure_reason=task.last_error if task and task.task_status == "failed" else None,
            can_retry=bool(task and task.task_status == "failed"),
            uploader_id=uploader.id if uploader else (job.requested_by if job else None),
            uploader_name=(uploader.display_name if uploader and uploader.display_name else (uploader.email if uploader else None)),
            uploader_email=(uploader.email if uploader else None),
        )
        for document, extraction, task, job, uploader in rows
    ]


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Document not found")

    extraction = (await db.execute(select(ReceiptExtraction).where(ReceiptExtraction.document_id == document_id))).scalars().first()
    classification = (
        await db.execute(
            select(ClassificationResult)
            .where(ClassificationResult.document_id == document_id)
            .order_by(ClassificationResult.id.desc())
            .limit(1)
        )
    ).scalars().first()
    review_task = (await db.execute(select(ReviewTask).where(ReviewTask.document_id == document_id))).scalars().first()

    return DocumentDetailResponse(
        id=document.id,
        original_filename=document.original_filename,
        storage_path=document.storage_path,
        document_status=document.document_status,
        extraction={
            "merchant_name": extraction.merchant_name,
            "transaction_date": str(extraction.transaction_date) if extraction and extraction.transaction_date else None,
            "registration_number": extraction.registration_number,
            "tax_amount": float(extraction.tax_amount) if extraction and extraction.tax_amount is not None else None,
            "total_amount": float(extraction.total_amount) if extraction and extraction.total_amount is not None else None,
            "tax_rate_label": extraction.tax_rate_label if extraction else None,
            "confidence": float(extraction.extraction_confidence) if extraction and extraction.extraction_confidence else None,
        }
        if extraction
        else None,
        classification={
            "subject_id": classification.subject_id,
            "confidence": float(classification.confidence_score) if classification and classification.confidence_score else None,
            "decision_source": classification.decision_source,
        }
        if classification
        else None,
        review_task={
            "task_id": review_task.id,
            "task_status": review_task.task_status,
            "reason_codes": review_task.reason_codes,
        }
        if review_task
        else None,
    )


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(document.storage_path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Stored file not found")

    media_type = document.mime_type or "application/octet-stream"
    return FileResponse(path=str(file_path), media_type=media_type, filename=document.original_filename)


@router.post("/{document_id}/re-ocr", status_code=202)
async def re_ocr_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    """Re-run OCR + classification asynchronously via the worker queue."""
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.document_status in ("queued", "ocr_processing", "classifying"):
        raise HTTPException(status_code=409, detail="Document is already being processed")

    task = (
        await db.execute(select(ProcessingTask).where(ProcessingTask.document_id == document_id))
    ).scalars().first()
    review_task = (
        await db.execute(select(ReviewTask).where(ReviewTask.document_id == document_id))
    ).scalars().first()
    if review_task is not None:
        await db.delete(review_task)

    if task is None:
        task = ProcessingTask(
            document_id=document_id,
            task_status="queued",
            attempts=0,
            max_attempts=settings.worker_max_attempts,
            next_run_at=datetime.utcnow(),
            started_at=None,
            finished_at=None,
            last_error=None,
        )
        db.add(task)
    else:
        task.task_status = "queued"
        task.attempts = 0
        task.max_attempts = settings.worker_max_attempts
        task.next_run_at = datetime.utcnow()
        task.started_at = None
        task.finished_at = None
        task.last_error = None

    document.document_status = "queued"
    await db.commit()
    return {"document_id": document_id, "task_status": "queued", "document_status": document.document_status}


@router.post("/{document_id}/retry-processing", status_code=202)
async def retry_processing(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    document = await db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if document.client_company_id != current_company.company_id:
        raise HTTPException(status_code=404, detail="Document not found")

    task = (
        await db.execute(select(ProcessingTask).where(ProcessingTask.document_id == document_id))
    ).scalars().first()

    if task is None:
        task = ProcessingTask(
            document_id=document_id,
            task_status="queued",
            attempts=0,
            max_attempts=settings.worker_max_attempts,
            next_run_at=datetime.utcnow(),
            started_at=None,
            finished_at=None,
            last_error=None,
        )
        db.add(task)
    else:
        task.task_status = "queued"
        task.attempts = 0
        task.max_attempts = settings.worker_max_attempts
        task.next_run_at = datetime.utcnow()
        task.started_at = None
        task.finished_at = None
        task.last_error = None

    document.document_status = "queued"
    await db.commit()
    return {"document_id": document_id, "task_status": "queued"}


@router.post("/re-ocr-bulk", status_code=202)
async def re_ocr_bulk(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    """Re-queue OCR for a list of document IDs owned by the current company.

    Accepts: {"document_ids": [1, 2, 3]}
    Skips documents that are already actively processing (queued/ocr_processing/classifying).
    Returns counts of queued and skipped documents.
    """
    raw_ids = payload.get("document_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=422, detail="document_ids must be a non-empty list")

    document_ids: list[int] = []
    for v in raw_ids:
        try:
            document_ids.append(int(v))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Invalid document id: {v!r}")

    if len(document_ids) > 500:
        raise HTTPException(status_code=422, detail="Batch limit is 500 documents per request")

    rows = (
        await db.execute(
            select(Document, ProcessingTask, ReviewTask)
            .outerjoin(ProcessingTask, ProcessingTask.document_id == Document.id)
            .outerjoin(ReviewTask, ReviewTask.document_id == Document.id)
            .where(Document.id.in_(document_ids))
            .where(Document.client_company_id == current_company.company_id)
        )
    ).all()

    now = datetime.utcnow()
    queued_ids: list[int] = []
    skipped_ids: list[int] = []

    for document, task, review_task in rows:
        if document.document_status in ("queued", "ocr_processing", "classifying"):
            skipped_ids.append(document.id)
            continue

        if review_task is not None:
            await db.delete(review_task)

        if task is None:
            db.add(ProcessingTask(
                document_id=document.id,
                task_status="queued",
                attempts=0,
                max_attempts=settings.worker_max_attempts,
                next_run_at=now,
                started_at=None,
                finished_at=None,
                last_error=None,
            ))
        else:
            task.task_status = "queued"
            task.attempts = 0
            task.max_attempts = settings.worker_max_attempts
            task.next_run_at = now
            task.started_at = None
            task.finished_at = None
            task.last_error = None

        document.document_status = "queued"
        queued_ids.append(document.id)

    await db.commit()
    return {"queued_count": len(queued_ids), "skipped_count": len(skipped_ids), "queued_ids": queued_ids, "skipped_ids": skipped_ids}


@router.post("/retry-failed", status_code=202)
async def retry_failed_processing_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_company: CurrentCompanyContext = Depends(get_current_company),
):
    rows = (
        await db.execute(
            select(Document, ProcessingTask)
            .join(ProcessingTask, ProcessingTask.document_id == Document.id)
            .where(ProcessingTask.task_status == "failed")
            .where(Document.client_company_id == current_company.company_id)
        )
    ).all()

    retried_document_ids: list[int] = []
    now = datetime.utcnow()

    for document, task in rows:
        task.task_status = "queued"
        task.attempts = 0
        task.max_attempts = settings.worker_max_attempts
        task.next_run_at = now
        task.started_at = None
        task.finished_at = None
        task.last_error = None
        document.document_status = "queued"
        retried_document_ids.append(document.id)

    await db.commit()
    return {"retried_count": len(retried_document_ids), "document_ids": retried_document_ids}
