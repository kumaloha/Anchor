import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.blogger import Blogger
from app.schemas.common import TaskResponse
from app.schemas.opinion import ManualIngestRequest, URLIngestRequest
from app.tasks.crawl_tasks import crawl_blogger_task, process_single_url_task

router = APIRouter(prefix="/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)


@router.post("/manual", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def manual_ingest(
    payload: ManualIngestRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """
    Manually submit raw text for opinion extraction and classification.
    Returns a Celery task ID.
    """
    blogger = await db.get(Blogger, payload.blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")

    task = crawl_blogger_task.apply_async(
        kwargs={
            "blogger_id": payload.blogger_id,
            "manual_text": payload.text,
            "source_quote": payload.source_quote,
            "language": payload.language,
            "domain_tags": payload.domain_tags,
            "topic_tags": payload.topic_tags,
        }
    )
    logger.info("Manual ingest task %s for blogger_id=%d", task.id, payload.blogger_id)
    return TaskResponse(task_id=task.id, message="Manual ingest queued")


@router.post("/crawl/{blogger_id}", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_crawl(
    blogger_id: int,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Trigger an immediate crawl for a specific blogger."""
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")

    task = crawl_blogger_task.apply_async(kwargs={"blogger_id": blogger_id})
    logger.info("Crawl task %s triggered for blogger_id=%d", task.id, blogger_id)
    return TaskResponse(task_id=task.id, message=f"Crawl queued for blogger '{blogger.name}'")


@router.post("/url", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_url(
    payload: URLIngestRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """
    Ingest a single URL (tweet or YouTube video).
    The URL is crawled, transcribed (if video), and opinions are extracted.
    """
    blogger = await db.get(Blogger, payload.blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")

    task = process_single_url_task.apply_async(
        kwargs={
            "blogger_id": payload.blogger_id,
            "url": payload.url,
        }
    )
    logger.info("URL ingest task %s for url='%s'", task.id, payload.url)
    return TaskResponse(task_id=task.id, message=f"URL ingest queued: {payload.url}")
