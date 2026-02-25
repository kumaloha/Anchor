import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.opinion import Opinion, OpinionStatusEnum, OpinionTypeEnum
from app.models.opinion_detail import (
    AdviceDetail,
    CommentaryDetail,
    HistoryDetail,
    PredictionDetail,
)
from app.schemas.opinion import (
    OpinionDetailOut,
    OpinionOut,
    OpinionUpdate,
    VerificationRecordOut,
)

router = APIRouter(prefix="/opinions", tags=["opinions"])
logger = logging.getLogger(__name__)

OPINION_LOAD_OPTIONS = [
    selectinload(Opinion.prediction_detail),
    selectinload(Opinion.history_detail),
    selectinload(Opinion.advice_detail),
    selectinload(Opinion.commentary_detail),
    selectinload(Opinion.verifications),
    selectinload(Opinion.blogger),
]


async def _get_opinion_or_404(opinion_id: int, db: AsyncSession) -> Opinion:
    stmt = (
        select(Opinion)
        .where(Opinion.id == opinion_id)
        .options(*OPINION_LOAD_OPTIONS)
    )
    result = await db.execute(stmt)
    opinion = result.scalar_one_or_none()
    if not opinion:
        raise HTTPException(status_code=404, detail="Opinion not found")
    return opinion


@router.get("", response_model=List[OpinionOut])
async def list_opinions(
    blogger_id: Optional[int] = Query(None),
    opinion_type: Optional[OpinionTypeEnum] = Query(None),
    status: Optional[OpinionStatusEnum] = Query(None),
    domain: Optional[str] = Query(None, description="Filter by domain tag (partial match)"),
    topic: Optional[str] = Query(None, description="Filter by topic tag (partial match)"),
    abstract_level: Optional[int] = Query(None, ge=1, le=3),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> List[Opinion]:
    """List opinions with flexible filtering and pagination."""
    stmt = select(Opinion).options(selectinload(Opinion.blogger))

    if blogger_id is not None:
        stmt = stmt.where(Opinion.blogger_id == blogger_id)
    if opinion_type is not None:
        stmt = stmt.where(Opinion.opinion_type == opinion_type)
    if status is not None:
        stmt = stmt.where(Opinion.status == status)
    if abstract_level is not None:
        stmt = stmt.where(Opinion.abstract_level == abstract_level)
    if domain:
        stmt = stmt.where(Opinion.domain_tags.any(domain))
    if topic:
        stmt = stmt.where(Opinion.topic_tags.any(topic))

    stmt = (
        stmt.order_by(Opinion.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{opinion_id}", response_model=OpinionDetailOut)
async def get_opinion(
    opinion_id: int,
    db: AsyncSession = Depends(get_db),
) -> Opinion:
    """Get a single opinion with all type-specific details and verification records."""
    return await _get_opinion_or_404(opinion_id, db)


@router.patch("/{opinion_id}", response_model=OpinionDetailOut)
async def update_opinion(
    opinion_id: int,
    payload: OpinionUpdate,
    db: AsyncSession = Depends(get_db),
) -> Opinion:
    """Update opinion attributes."""
    opinion = await _get_opinion_or_404(opinion_id, db)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(opinion, field, value)
    db.add(opinion)
    await db.flush()
    await db.refresh(opinion)
    # Reload relations
    return await _get_opinion_or_404(opinion_id, db)


@router.delete("/{opinion_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_opinion(
    opinion_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an opinion and all related data."""
    stmt = select(Opinion).where(Opinion.id == opinion_id)
    result = await db.execute(stmt)
    opinion = result.scalar_one_or_none()
    if not opinion:
        raise HTTPException(status_code=404, detail="Opinion not found")
    await db.delete(opinion)
    logger.info("Deleted opinion id=%d", opinion_id)


@router.get("/{opinion_id}/verifications", response_model=List[VerificationRecordOut])
async def list_verifications(
    opinion_id: int,
    db: AsyncSession = Depends(get_db),
) -> list:
    """List all verification records for an opinion."""
    opinion = await _get_opinion_or_404(opinion_id, db)
    return opinion.verifications
