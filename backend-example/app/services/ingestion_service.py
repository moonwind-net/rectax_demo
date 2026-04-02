import hashlib
import logging
import mimetypes
import os
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.category import ClassificationResult
from ..models.receipt import (
    Document,
    DocumentFlag,
    IngestionJob,
    IngestionSource,
    LocalFolderWatch,
    ProcessingTask,
    ReviewTask,
)
from .classification_service import ClassificationService
from .ocr_service import OcrService

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, db: AsyncSession, accounting_firm_id: int | None = None, client_company_id: int | None = None):
        self.db = db
        self.ocr_service = OcrService(db)
        self.classification_service = ClassificationService(db)
        self.accounting_firm_id = accounting_firm_id or settings.default_firm_id
        self.client_company_id = client_company_id or settings.default_company_id

    async def upload_and_enqueue(self, files: list[UploadFile], requested_by_user_id: int | None = None) -> tuple[int, list[int], list[int]]:
        source = await self._get_or_create_upload_source()

        known_stmt = select(Document.file_hash_sha256, Document.id).where(
            Document.accounting_firm_id == self.accounting_firm_id,
            Document.client_company_id == self.client_company_id,
            Document.file_hash_sha256.is_not(None),
        )
        known_rows = (await self.db.execute(known_stmt)).all()
        known_hash_to_id: dict[str, int] = {row[0]: row[1] for row in known_rows}

        job = IngestionJob(
            accounting_firm_id=self.accounting_firm_id,
            client_company_id=self.client_company_id,
            ingestion_source_id=source.id,
            requested_by=requested_by_user_id or settings.default_user_id,
            job_status="queued",
            total_files=len(files),
        )
        self.db.add(job)
        await self.db.flush()

        max_total_bytes = settings.upload_max_total_mb_per_request * 1024 * 1024
        total_bytes_read = 0

        document_ids: list[int] = []
        duplicate_document_ids: list[int] = []
        for file in files:
            file_bytes = await file.read()

            # ── Failsafe byte-level size guards (catch clients that forge or
            #    omit Content-Length headers on individual multipart parts) ──
            if len(file_bytes) > settings.max_file_size:
                raise ValueError(
                    f"ファイル {file.filename!r} がサイズ上限を超えています "
                    f"({len(file_bytes) // 1024} KB > {settings.max_file_size // 1024} KB)"
                )
            total_bytes_read += len(file_bytes)
            if total_bytes_read > max_total_bytes:
                raise ValueError(
                    f"合計ファイルサイズが上限 {settings.upload_max_total_mb_per_request} MB を超えました。"
                )

            file_hash = hashlib.sha256(file_bytes).hexdigest()
            if file_hash in known_hash_to_id:
                logger.info("Skipping duplicate upload: %s", file.filename)
                duplicate_document_ids.append(known_hash_to_id[file_hash])
                continue

            known_hash_to_id[file_hash] = -1
            file_path, file_hash = self._save_file(file.filename or "unknown.bin", file_bytes)

            document = Document(
                accounting_firm_id=self.accounting_firm_id,
                client_company_id=self.client_company_id,
                ingestion_job_id=job.id,
                ingestion_source_id=source.id,
                original_filename=file.filename or "unknown.bin",
                storage_path=file_path,
                file_ext=Path(file.filename or "").suffix.lower(),
                mime_type=file.content_type,
                file_size_bytes=len(file_bytes),
                file_hash_sha256=file_hash,
                document_status="uploaded",
            )
            self.db.add(document)
            await self.db.flush()
            known_hash_to_id[file_hash] = document.id

            self.db.add(
                ProcessingTask(
                    document_id=document.id,
                    task_status="queued",
                    attempts=0,
                    max_attempts=settings.worker_max_attempts,
                    next_run_at=datetime.utcnow(),
                )
            )
            document_ids.append(document.id)

        # Uploaded/queued into DB task table. Worker will update success/failed counts.
        job.success_files = 0
        job.failed_files = 0
        job.job_status = "queued" if len(document_ids) > 0 else "completed"

        await self.db.commit()
        return job.id, document_ids, duplicate_document_ids

    async def upload_and_process(self, files: list[UploadFile], requested_by_user_id: int | None = None) -> tuple[int, list[int], list[int]]:
        """Backward-compatible wrapper.

        Existing callers should migrate to upload_and_enqueue. Keeping this method
        avoids breaking other code paths while moving to async worker processing.
        """
        return await self.upload_and_enqueue(files, requested_by_user_id=requested_by_user_id)

    async def _process_document_pipeline(self, document: Document) -> None:
        # Delete stale flags so re-runs don't accumulate duplicates
        old_flags = (
            await self.db.execute(
                select(DocumentFlag).where(DocumentFlag.document_id == document.id)
            )
        ).scalars().all()
        for flag in old_flags:
            await self.db.delete(flag)
        await self.db.flush()

        document.document_status = "ocr_processing"
        _, extraction = await self.ocr_service.process_document(document)
        document.document_status = "classifying"
        classification: ClassificationResult = await self.classification_service.classify_document(document, extraction)

        needs_review, reasons = await self.classification_service.should_send_review(classification, extraction)
        if needs_review:
            document.document_status = "review_required"
            self.db.add(
                ReviewTask(
                    document_id=document.id,
                    task_status="pending",
                    reason_codes=reasons,
                    assigned_to=settings.default_user_id,
                )
            )
            for reason in reasons:
                self.db.add(
                    DocumentFlag(
                        document_id=document.id,
                        flag_code=reason,
                        severity="medium",
                        message=f"Auto flag: {reason}",
                    )
                )
        else:
            document.document_status = "classified"

    async def get_pending_tasks(self, limit: int) -> list[ProcessingTask]:
        now = datetime.utcnow()
        stmt = (
            select(ProcessingTask)
            .where(ProcessingTask.task_status == "queued")
            .where((ProcessingTask.next_run_at.is_(None)) | (ProcessingTask.next_run_at <= now))
            .order_by(ProcessingTask.id.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_processing_backlog(self) -> int:
        stmt = (
            select(func.count())
            .select_from(ProcessingTask)
            .where(ProcessingTask.task_status.in_(("queued", "processing")))
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def process_task(self, task: ProcessingTask) -> None:
        document = await self.db.get(Document, task.document_id)
        if not document:
            task.task_status = "failed"
            task.finished_at = datetime.utcnow()
            task.last_error = "Document not found"
            await self.db.commit()
            return

        task.task_status = "processing"
        task.started_at = datetime.utcnow()
        task.last_error = None
        await self.db.flush()

        try:
            await self._process_document_pipeline(document)
            task.task_status = "completed"
            task.finished_at = datetime.utcnow()
            task.next_run_at = None
        except Exception as exc:  # noqa: BLE001
            task.attempts += 1
            task.last_error = str(exc)[:2000]
            if task.attempts >= task.max_attempts:
                task.task_status = "failed"
                task.finished_at = datetime.utcnow()
                document.document_status = "failed"
            else:
                task.task_status = "queued"
                document.document_status = "queued"
                delay_s = settings.worker_retry_base_seconds * (2 ** (task.attempts - 1))
                task.next_run_at = datetime.utcnow() + timedelta(seconds=delay_s)
        finally:
            await self._refresh_job_status(document.ingestion_job_id)
            await self.db.commit()

    async def _refresh_job_status(self, ingestion_job_id: int | None) -> None:
        if ingestion_job_id is None:
            return

        job = await self.db.get(IngestionJob, ingestion_job_id)
        if not job:
            return

        success_statuses = ("classified", "review_required")
        failed_statuses = ("failed",)

        success_files = (
            await self.db.execute(
                select(func.count()).select_from(Document).where(
                    Document.ingestion_job_id == ingestion_job_id,
                    Document.document_status.in_(success_statuses),
                )
            )
        ).scalar_one()
        failed_files = (
            await self.db.execute(
                select(func.count()).select_from(Document).where(
                    Document.ingestion_job_id == ingestion_job_id,
                    Document.document_status.in_(failed_statuses),
                )
            )
        ).scalar_one()

        pending_files = (
            await self.db.execute(
                select(func.count()).select_from(Document).where(
                    Document.ingestion_job_id == ingestion_job_id,
                    ~Document.document_status.in_(success_statuses + failed_statuses),
                )
            )
        ).scalar_one()

        job.success_files = int(success_files)
        job.failed_files = int(failed_files)
        if pending_files > 0:
            job.job_status = "running"
        elif failed_files > 0:
            job.job_status = "partial"
        else:
            job.job_status = "completed"

    async def _get_or_create_upload_source(self) -> IngestionSource:
        stmt = select(IngestionSource).where(
            IngestionSource.accounting_firm_id == self.accounting_firm_id,
            IngestionSource.client_company_id == self.client_company_id,
            IngestionSource.source_type == "upload",
        )
        source = (await self.db.execute(stmt)).scalars().first()
        if source:
            return source

        source = IngestionSource(
            accounting_firm_id=self.accounting_firm_id,
            client_company_id=self.client_company_id,
            source_type="upload",
            source_name="Manual Upload",
            created_by=settings.default_user_id,
        )
        self.db.add(source)
        await self.db.flush()
        return source

    def _save_file(self, file_name: str, content: bytes) -> tuple[str, str]:
        os.makedirs(settings.upload_dir, exist_ok=True)
        target = os.path.join(settings.upload_dir, file_name)
        base, ext = os.path.splitext(target)
        suffix = 1
        while os.path.exists(target):
            target = f"{base}_{suffix}{ext}"
            suffix += 1

        with open(target, "wb") as f:
            f.write(content)

        file_hash = hashlib.sha256(content).hexdigest()
        return target, file_hash

    # ------------------------------------------------------------------
    # Local folder watch
    # ------------------------------------------------------------------

    async def list_folder_sources(self) -> list[dict]:
        stmt = (
            select(IngestionSource, LocalFolderWatch)
            .outerjoin(LocalFolderWatch, LocalFolderWatch.ingestion_source_id == IngestionSource.id)
            .where(IngestionSource.source_type == "local_folder")
            .where(IngestionSource.accounting_firm_id == settings.default_firm_id)
            .where(IngestionSource.client_company_id == self.client_company_id)
        )
        rows = (await self.db.execute(stmt)).all()
        result = []
        for source, watch in rows:
            entry: dict = {
                "id": source.id,
                "source_name": source.source_name,
                "is_active": source.is_active,
                "folder_path": watch.folder_path if watch else None,
                "recursive": watch.recursive if watch else True,
                "include_pattern": watch.include_pattern if watch else "*",
                "scan_interval_minutes": watch.scan_interval_minutes if watch else 30,
            }
            result.append(entry)
        return result

    async def scan_folder_watch(self, source_id: int) -> tuple[int, list[int]]:
        """Scan a local folder watch source and ingest new files."""
        stmt = (
            select(IngestionSource, LocalFolderWatch)
            .join(LocalFolderWatch, LocalFolderWatch.ingestion_source_id == IngestionSource.id)
            .where(IngestionSource.id == source_id)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            raise ValueError(f"Folder watch source {source_id} not found")

        source, watch = row
        folder = Path(watch.folder_path)
        if not folder.exists():
            raise ValueError(f"Watch folder does not exist: {folder}")

        # Collect already-known hashes to skip duplicates
        known_stmt = select(Document.file_hash_sha256).where(
            Document.accounting_firm_id == self.accounting_firm_id,
            Document.client_company_id == self.client_company_id,
            Document.file_hash_sha256.is_not(None),
        )
        known_hashes: set[str] = set((await self.db.execute(known_stmt)).scalars().all())

        # Glob files
        pattern = watch.include_pattern or "*"
        if watch.recursive:
            candidate_files = list(folder.rglob(pattern))
        else:
            candidate_files = list(folder.glob(pattern))

        # Filter to supported image/pdf types only
        allowed_exts = {".jpg", ".jpeg", ".png", ".pdf", ".tiff", ".tif", ".webp"}
        candidate_files = [p for p in candidate_files if p.is_file() and p.suffix.lower() in allowed_exts]

        if not candidate_files:
            return 0, []

        job = IngestionJob(
            accounting_firm_id=self.accounting_firm_id,
            client_company_id=self.client_company_id,
            ingestion_source_id=source.id,
            requested_by=settings.default_user_id,
            job_status="running",
            total_files=len(candidate_files),
        )
        self.db.add(job)
        await self.db.flush()

        document_ids: list[int] = []
        for file_path in candidate_files:
            content = file_path.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()
            if file_hash in known_hashes:
                logger.debug("Skipping already-ingested file: %s", file_path)
                continue

            known_hashes.add(file_hash)
            dest, _ = self._save_file(file_path.name, content)
            mime_type, _ = mimetypes.guess_type(str(file_path))

            document = Document(
                accounting_firm_id=self.accounting_firm_id,
                client_company_id=self.client_company_id,
                ingestion_job_id=job.id,
                ingestion_source_id=source.id,
                original_filename=file_path.name,
                storage_path=dest,
                file_ext=file_path.suffix.lower(),
                mime_type=mime_type,
                file_size_bytes=len(content),
                file_hash_sha256=file_hash,
                document_status="ocr_processing",
            )
            self.db.add(document)
            await self.db.flush()

            await self._process_document_pipeline(document)
            document_ids.append(document.id)

        job.success_files = len(document_ids)
        job.failed_files = max(0, len(candidate_files) - len(document_ids) - (len(candidate_files) - job.total_files))
        job.job_status = "completed"
        await self.db.commit()
        return job.id, document_ids
