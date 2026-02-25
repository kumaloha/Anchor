import asyncio
import logging
from typing import Optional

from sqlalchemy.orm import selectinload

from app.core.celery_app import celery_app
from app.core.database import get_db_context
from app.models.opinion import Opinion, OpinionStatusEnum, OpinionTypeEnum
from app.models.opinion_detail import (
    AdviceDetail,
    AssumptionLevelEnum,
    CommentaryDetail,
    HistoryDetail,
    PredictionDetail,
    PredictionVerificationStatusEnum,
    SentimentEnum,
)
from app.models.raw_content import RawContent
from app.services.extractors.opinion_extractor import OpinionExtractor
from app.services.processors.classifier import (
    AdviceAttributes,
    Classifier,
    CommentaryAttributes,
    HistoryAttributes,
    PredictionAttributes,
)
from app.services.trackers.advice_tracker import AdviceTracker
from app.services.trackers.commentary_tracker import CommentaryTracker
from app.services.trackers.history_tracker import HistoryTracker
from app.services.trackers.prediction_tracker import PredictionTracker

logger = logging.getLogger(__name__)

extractor = OpinionExtractor()
classifier = Classifier()

_trackers = {
    "prediction": PredictionTracker(),
    "history": HistoryTracker(),
    "advice": AdviceTracker(),
    "commentary": CommentaryTracker(),
}


async def _do_process_raw_content(raw_content_id: int) -> dict:
    """
    Core pipeline:
    1. Load RawContent
    2. Extract opinions via LLM
    3. Classify each opinion
    4. Persist opinions + type-specific details
    5. Mark RawContent as processed
    """
    async with get_db_context() as db:
        from sqlalchemy import select

        stmt = (
            select(RawContent)
            .where(RawContent.id == raw_content_id)
            .options(selectinload(RawContent.blogger))
        )
        result = await db.execute(stmt)
        rc = result.scalar_one_or_none()

        if not rc:
            logger.error("process_raw_content_task: raw_content id=%d not found", raw_content_id)
            return {"status": "error", "reason": "not_found"}

        if rc.is_processed:
            logger.info("process_raw_content_task: raw_content id=%d already processed", raw_content_id)
            return {"status": "skipped", "reason": "already_processed"}

        # Choose the best text to extract opinions from
        source_text = rc.transcript or rc.raw_text or ""
        if not source_text.strip():
            logger.warning("process_raw_content_task: no text in raw_content id=%d", raw_content_id)
            rc.is_processed = True
            db.add(rc)
            return {"status": "skipped", "reason": "no_text"}

        blogger_context = (
            f"{rc.blogger.name} — {rc.blogger.platform.value}" if rc.blogger else None
        )

        # 1. Extract opinions
        extracted = await extractor.extract(source_text, blogger_context=blogger_context)
        logger.info(
            "Extracted %d opinions from raw_content id=%d",
            len(extracted),
            raw_content_id,
        )

        opinion_ids = []

        for ext_op in extracted:
            # 2. Classify
            classification = await classifier.classify(ext_op.text)
            if classification is None:
                logger.warning("Classifier returned None for opinion text '%s...'", ext_op.text[:60])
                continue

            attrs = classification.attributes

            # Map type enum
            type_map = {
                "prediction": OpinionTypeEnum.prediction,
                "history": OpinionTypeEnum.history,
                "advice": OpinionTypeEnum.advice,
                "commentary": OpinionTypeEnum.commentary,
            }
            opinion_type = type_map.get(attrs.opinion_type, OpinionTypeEnum.commentary)

            # 3. Create Opinion record
            opinion = Opinion(
                blogger_id=rc.blogger_id,
                raw_content_id=rc.id,
                text=ext_op.text,
                abstract_level=ext_op.abstract_level,
                opinion_type=opinion_type,
                status=OpinionStatusEnum.pending,
                importance=ext_op.importance,
                influence=3,  # default
                language=ext_op.language,
                source_quote=ext_op.source_quote,
            )
            db.add(opinion)
            await db.flush()
            await db.refresh(opinion)

            # 4. Create type-specific detail
            if isinstance(attrs, PredictionAttributes):
                deadline = classifier.parse_deadline(attrs.deadline)
                detail = PredictionDetail(
                    opinion_id=opinion.id,
                    prediction_summary=attrs.prediction_summary,
                    deadline=deadline,
                    verification_status=PredictionVerificationStatusEnum.pending,
                )
                db.add(detail)

            elif isinstance(attrs, HistoryAttributes):
                assumption_map = {
                    "none": AssumptionLevelEnum.none,
                    "low": AssumptionLevelEnum.low,
                    "medium": AssumptionLevelEnum.medium,
                    "high": AssumptionLevelEnum.high,
                }
                assumption_level = assumption_map.get(
                    attrs.assumption_level.lower(), AssumptionLevelEnum.medium
                )
                detail = HistoryDetail(
                    opinion_id=opinion.id,
                    claim_summary=attrs.claim_summary,
                    is_complete=attrs.is_complete,
                    assumption_level=assumption_level,
                    has_assumptions=attrs.has_assumptions,
                    assumption_list=attrs.assumption_list,
                    can_verify=attrs.is_complete and not attrs.has_assumptions,
                )
                db.add(detail)

            elif isinstance(attrs, AdviceAttributes):
                detail = AdviceDetail(
                    opinion_id=opinion.id,
                    advice_summary=attrs.advice_summary,
                    basis=attrs.basis,
                    rarity_score=attrs.rarity_score,
                    importance_score=attrs.importance_score,
                )
                db.add(detail)

            elif isinstance(attrs, CommentaryAttributes):
                sentiment_map = {
                    "positive": SentimentEnum.positive,
                    "negative": SentimentEnum.negative,
                    "neutral": SentimentEnum.neutral,
                    "mixed": SentimentEnum.mixed,
                }
                sentiment = sentiment_map.get(attrs.sentiment, SentimentEnum.neutral)
                detail = CommentaryDetail(
                    opinion_id=opinion.id,
                    sentiment=sentiment,
                    target_subject=attrs.target_subject,
                )
                db.add(detail)

            opinion_ids.append(opinion.id)

        # Mark raw_content as processed
        rc.is_processed = True
        db.add(rc)

        logger.info(
            "process_raw_content_task: created %d opinions from raw_content id=%d",
            len(opinion_ids),
            raw_content_id,
        )
        return {"status": "ok", "opinion_ids": opinion_ids}


@celery_app.task(
    name="app.tasks.process_tasks.process_raw_content_task",
    bind=True,
    max_retries=3,
)
def process_raw_content_task(self, raw_content_id: int) -> dict:
    """
    Celery task: extract opinions from a RawContent record and classify them.
    """
    try:
        result = asyncio.get_event_loop().run_until_complete(
            _do_process_raw_content(raw_content_id)
        )
        return result
    except Exception as exc:
        logger.error("process_raw_content_task failed for id=%d: %s", raw_content_id, exc)
        raise self.retry(exc=exc, countdown=30)


async def _do_track_opinion(opinion_id: int) -> dict:
    """Run the appropriate tracker for an opinion."""
    async with get_db_context() as db:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        stmt = (
            select(Opinion)
            .where(Opinion.id == opinion_id)
            .options(
                selectinload(Opinion.prediction_detail),
                selectinload(Opinion.history_detail),
                selectinload(Opinion.advice_detail),
                selectinload(Opinion.commentary_detail),
                selectinload(Opinion.blogger),
                selectinload(Opinion.verifications),
            )
        )
        result = await db.execute(stmt)
        opinion = result.scalar_one_or_none()

        if not opinion:
            logger.error("track_opinion_task: opinion id=%d not found", opinion_id)
            return {"status": "error", "reason": "not_found"}

        tracker = _trackers.get(opinion.opinion_type.value)
        if not tracker:
            logger.warning("track_opinion_task: no tracker for type '%s'", opinion.opinion_type)
            return {"status": "skipped", "reason": "no_tracker"}

        await tracker.track(opinion, db)
        logger.info("track_opinion_task: completed for opinion id=%d", opinion_id)
        return {"status": "ok", "opinion_id": opinion_id}


@celery_app.task(
    name="app.tasks.process_tasks.track_opinion_task",
    bind=True,
    max_retries=3,
)
def track_opinion_task(self, opinion_id: int) -> dict:
    """Celery task: run the appropriate tracker for an opinion."""
    try:
        result = asyncio.get_event_loop().run_until_complete(
            _do_track_opinion(opinion_id)
        )
        return result
    except Exception as exc:
        logger.error("track_opinion_task failed for opinion_id=%d: %s", opinion_id, exc)
        raise self.retry(exc=exc, countdown=60)
