import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.blogger import Blogger
from app.schemas.blogger import BloggerCreate, BloggerOut, BloggerUpdate

router = APIRouter(prefix="/bloggers", tags=["bloggers"])
logger = logging.getLogger(__name__)


@router.post("", response_model=BloggerOut, status_code=status.HTTP_201_CREATED)
async def create_blogger(
    payload: BloggerCreate,
    db: AsyncSession = Depends(get_db),
) -> Blogger:
    """Add a new blogger/vlogger to track."""
    blogger = Blogger(
        platform=payload.platform,
        url=payload.url,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    db.add(blogger)
    await db.flush()
    await db.refresh(blogger)
    logger.info("Created blogger id=%d name='%s'", blogger.id, blogger.name)
    return blogger


@router.get("", response_model=List[BloggerOut])
async def list_bloggers(
    is_active: bool | None = None,
    db: AsyncSession = Depends(get_db),
) -> List[Blogger]:
    """List all tracked bloggers, optionally filtered by active status."""
    stmt = select(Blogger).order_by(Blogger.created_at.desc())
    if is_active is not None:
        stmt = stmt.where(Blogger.is_active == is_active)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{blogger_id}", response_model=BloggerOut)
async def get_blogger(
    blogger_id: int,
    db: AsyncSession = Depends(get_db),
) -> Blogger:
    """Get a single blogger by ID."""
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")
    return blogger


@router.patch("/{blogger_id}", response_model=BloggerOut)
async def update_blogger(
    blogger_id: int,
    payload: BloggerUpdate,
    db: AsyncSession = Depends(get_db),
) -> Blogger:
    """Update blogger attributes."""
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(blogger, field, value)
    db.add(blogger)
    await db.flush()
    await db.refresh(blogger)
    return blogger


@router.delete("/{blogger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blogger(
    blogger_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a blogger and all associated data."""
    blogger = await db.get(Blogger, blogger_id)
    if not blogger:
        raise HTTPException(status_code=404, detail="Blogger not found")
    await db.delete(blogger)
    logger.info("Deleted blogger id=%d", blogger_id)
