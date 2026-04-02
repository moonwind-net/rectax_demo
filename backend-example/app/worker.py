"""Minimal DB-polling worker for async OCR/classification processing.

This worker reads tasks from processing_tasks table and executes document
pipeline sequentially with bounded batch size each poll cycle.
"""

import asyncio
import logging

from sqlalchemy import select

from . import models  # noqa: F401
from .config import settings
from .database import AsyncSessionLocal, Base, engine
from .models.receipt import ProcessingTask
from .services.ingestion_service import IngestionService


logger = logging.getLogger(__name__)


async def _process_one_task(task_id: int, sem: asyncio.Semaphore) -> None:
    async with sem:
        async with AsyncSessionLocal() as db:
            service = IngestionService(db)
            task = (
                await db.execute(select(ProcessingTask).where(ProcessingTask.id == task_id))
            ).scalars().first()
            if task is None or task.task_status != "queued":
                return
            await service.process_task(task)


async def _run_worker_forever() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(
        "Worker started (poll=%ss, batch=%s, concurrency=%s)",
        settings.worker_poll_interval_seconds,
        settings.worker_batch_size,
        settings.worker_concurrency,
    )

    sem = asyncio.Semaphore(max(1, settings.worker_concurrency))

    while True:
        try:
            async with AsyncSessionLocal() as db:
                service = IngestionService(db)
                tasks = await service.get_pending_tasks(settings.worker_batch_size)
                if not tasks:
                    await asyncio.sleep(settings.worker_poll_interval_seconds)
                    continue

                task_ids = [t.id for t in tasks]

            await asyncio.gather(*(_process_one_task(task_id, sem) for task_id in task_ids))

        except Exception as exc:  # noqa: BLE001
            logger.exception("Worker loop error: %s", exc)
            await asyncio.sleep(settings.worker_poll_interval_seconds)


def main() -> None:
    asyncio.run(_run_worker_forever())


if __name__ == "__main__":
    main()
