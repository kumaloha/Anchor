import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Optional

from celery import shared_task
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.database import get_db_context
from app.models.blogger import Blogger
from app.models.raw_content import RawContent
from app.services.collectors.twitter import TwitterCollector
from app.services.collectors.youtube import YouTubeCollector
from app.tasks.process_tasks import process_raw_content_task

logger = logging.getLogger(__name__)

_twitter_collector = TwitterCollector()
_youtube_collector = YouTubeCollector()


def _get_collector(platform: str):
    if _twitter_collector.supports_platform(platform):
        return _twitter_collector
    if _youtube_collector.supports_platform(platform):
        return _youtube_collector
    return None


async def _do_crawl_blogger(
    blogger_id: int,
    manual_text: Optional[str] = None,
    source_quote: Optional[str] = None,
    language: Optional[str] = None,
    domain_tags: Optional[List[str]] = None,
    topic_tags: Optional[List[str]] = None,
) -> List[int]:
    """Core async logic for crawling a blogger and saving RawContent records."""
    raw_content_ids: List[int] = []

    async with get_db_context() as db:
        blogger = await db.get(Blogger, blogger_id)
        if not blogger:
            logger.error("crawl_blogger_task: blogger_id=%d not found", blogger_id)
            return []

        if manual_text:
            # Manual ingest path: create a RawContent directly
            rc = RawContent(
                blogger_id=blogger.id,
                platform=blogger.platform.value,
                content_type="text",
                raw_text=manual_text,
                source_url=None,
                source_id=None,
                is_processed=False,
            )
            db.add(rc)
            await db.flush()
            await db.refresh(rc)
            raw_content_ids.append(rc.id)
            logger.info("Manual ingest: created raw_content id=%d", rc.id)
        else:
            # Platform crawl path
            collector = _get_collector(blogger.platform.value)
            if not collector:
                logger.warning(
                    "crawl_blogger_task: no collector for platform '%s'",
                    blogger.platform.value,
                )
                return []

            new_contents = await collector.fetch(blogger)

            # Filter already-seen source_ids
            if new_contents:
                existing_ids_result = await db.execute(
                    select(RawContent.source_id).where(
                        RawContent.blogger_id == blogger_id,
                        RawContent.source_id.in_(
                            [rc.source_id for rc in new_contents if rc.source_id]
                        ),
                    )
                )
                existing_ids = set(existing_ids_result.scalars().all())
                new_contents = [
                    rc for rc in new_contents
                    if not rc.source_id or rc.source_id not in existing_ids
                ]

            for rc in new_contents:
                db.add(rc)
            await db.flush()
            for rc in new_contents:
                await db.refresh(rc)
                raw_content_ids.append(rc.id)

            # Update last_crawled_at
            blogger.last_crawled_at = datetime.now(timezone.utc)
            db.add(blogger)

            logger.info(
                "crawl_blogger_task: saved %d new raw_content records for blogger_id=%d",
                len(new_contents),
                blogger_id,
            )

    # Enqueue processing tasks for each raw content
    for rc_id in raw_content_ids:
        process_raw_content_task.apply_async(kwargs={"raw_content_id": rc_id})

    return raw_content_ids


@celery_app.task(name="app.tasks.crawl_tasks.crawl_blogger_task", bind=True, max_retries=3)
def crawl_blogger_task(
    self,
    blogger_id: int,
    manual_text: Optional[str] = None,
    source_quote: Optional[str] = None,
    language: Optional[str] = None,
    domain_tags: Optional[List[str]] = None,
    topic_tags: Optional[List[str]] = None,
) -> dict:
    """
    Celery task: crawl a blogger (or process manual input) and
    enqueue process_raw_content_task for each new item.
    """
    try:
        raw_content_ids = asyncio.get_event_loop().run_until_complete(
            _do_crawl_blogger(
                blogger_id=blogger_id,
                manual_text=manual_text,
                source_quote=source_quote,
                language=language,
                domain_tags=domain_tags,
                topic_tags=topic_tags,
            )
        )
        return {"status": "ok", "raw_content_ids": raw_content_ids}
    except Exception as exc:
        logger.error("crawl_blogger_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="app.tasks.crawl_tasks.crawl_all_active_bloggers")
def crawl_all_active_bloggers() -> dict:
    """
    Beat task: crawl all active bloggers.
    Dispatches individual crawl_blogger_task for each active blogger.
    """
    async def _fetch_active_blogger_ids() -> List[int]:
        async with get_db_context() as db:
            result = await db.execute(
                select(Blogger.id).where(Blogger.is_active == True)
            )
            return list(result.scalars().all())

    blogger_ids = asyncio.get_event_loop().run_until_complete(_fetch_active_blogger_ids())

    for bid in blogger_ids:
        crawl_blogger_task.apply_async(kwargs={"blogger_id": bid})

    logger.info("crawl_all_active_bloggers: dispatched %d crawl tasks", len(blogger_ids))
    return {"status": "ok", "dispatched": len(blogger_ids)}


async def _do_process_single_url(blogger_id: int, url: str) -> Optional[int]:
    """Download and process a single URL (tweet or YouTube video)."""
    async with get_db_context() as db:
        blogger = await db.get(Blogger, blogger_id)
        if not blogger:
            return None

        collector = _get_collector(blogger.platform.value)
        if not collector:
            # Try to guess from URL
            if "youtube" in url or "youtu.be" in url:
                collector = _youtube_collector
            else:
                collector = _twitter_collector

        # For single URL: create a minimal mock and call collector
        # YouTube collector supports single video URL directly
        if "youtube" in url or "youtu.be" in url:
            contents = await _youtube_collector._process_single_video(url, blogger)
        else:
            # For X: not practical without username resolution — store raw text placeholder
            contents = []

        for rc in contents:
            db.add(rc)
        await db.flush()
        if contents:
            await db.refresh(contents[0])
            return contents[0].id
    return None


@celery_app.task(name="app.tasks.crawl_tasks.process_single_url_task", bind=True, max_retries=2)
def process_single_url_task(self, blogger_id: int, url: str) -> dict:
    """Process a single URL: crawl, transcribe if video, then extract opinions."""
    try:
        rc_id = asyncio.get_event_loop().run_until_complete(
            _do_process_single_url(blogger_id, url)
        )
        if rc_id:
            process_raw_content_task.apply_async(kwargs={"raw_content_id": rc_id})
            return {"status": "ok", "raw_content_id": rc_id}
        return {"status": "skipped", "reason": "no content extracted"}
    except Exception as exc:
        logger.error("process_single_url_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=30)
