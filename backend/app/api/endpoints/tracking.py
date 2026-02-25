import logging
from collections import defaultdict
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.opinion import Opinion, OpinionStatusEnum, OpinionTypeEnum
from app.models.opinion_detail import PredictionDetail, PredictionVerificationStatusEnum
from app.models.verification import VerificationRecord
from app.schemas.common import TaskResponse
from app.schemas.opinion import TrackingSummary
from app.tasks.process_tasks import track_opinion_task

router = APIRouter(prefix="/tracking", tags=["tracking"])
logger = logging.getLogger(__name__)


@router.post("/run/{opinion_id}", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_tracking(
    opinion_id: int,
    db: AsyncSession = Depends(get_db),
) -> TaskResponse:
    """Trigger tracking/verification for a specific opinion."""
    opinion = await db.get(Opinion, opinion_id)
    if not opinion:
        raise HTTPException(status_code=404, detail="Opinion not found")

    task = track_opinion_task.apply_async(kwargs={"opinion_id": opinion_id})
    logger.info("Tracking task %s triggered for opinion_id=%d", task.id, opinion_id)
    return TaskResponse(task_id=task.id, message=f"Tracking queued for opinion {opinion_id}")


@router.get("/summary", response_model=TrackingSummary)
async def tracking_summary(
    db: AsyncSession = Depends(get_db),
) -> TrackingSummary:
    """Get an overview of opinion tracking statistics."""

    # Total opinions
    total_result = await db.execute(select(func.count()).select_from(Opinion))
    total_opinions = total_result.scalar_one()

    # By type
    type_result = await db.execute(
        select(Opinion.opinion_type, func.count()).group_by(Opinion.opinion_type)
    )
    by_type: Dict[str, int] = {row[0].value: row[1] for row in type_result.all()}
    for t in OpinionTypeEnum:
        by_type.setdefault(t.value, 0)

    # By status
    status_result = await db.execute(
        select(Opinion.status, func.count()).group_by(Opinion.status)
    )
    by_status: Dict[str, int] = {row[0].value: row[1] for row in status_result.all()}
    for s in OpinionStatusEnum:
        by_status.setdefault(s.value, 0)

    # Pending verifications (prediction type with pending status)
    pending_result = await db.execute(
        select(func.count())
        .select_from(PredictionDetail)
        .where(PredictionDetail.verification_status == PredictionVerificationStatusEnum.pending)
    )
    pending_verifications = pending_result.scalar_one()

    # Verified true
    vtrue_result = await db.execute(
        select(func.count())
        .select_from(PredictionDetail)
        .where(PredictionDetail.verification_status == PredictionVerificationStatusEnum.verified_true)
    )
    verified_true = vtrue_result.scalar_one()

    # Verified false
    vfalse_result = await db.execute(
        select(func.count())
        .select_from(PredictionDetail)
        .where(PredictionDetail.verification_status == PredictionVerificationStatusEnum.verified_false)
    )
    verified_false = vfalse_result.scalar_one()

    return TrackingSummary(
        total_opinions=total_opinions,
        by_type=by_type,
        by_status=by_status,
        pending_verifications=pending_verifications,
        verified_true=verified_true,
        verified_false=verified_false,
    )
