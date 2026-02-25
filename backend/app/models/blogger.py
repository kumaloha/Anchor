import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.raw_content import RawContent
    from app.models.opinion import Opinion


class PlatformEnum(str, enum.Enum):
    x = "x"
    youtube = "youtube"
    manual = "manual"


class Blogger(Base):
    __tablename__ = "bloggers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    platform: Mapped[PlatformEnum] = mapped_column(
        Enum(PlatformEnum, name="platform_enum"), nullable=False
    )
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    raw_contents: Mapped[List["RawContent"]] = relationship(
        "RawContent", back_populates="blogger", cascade="all, delete-orphan"
    )
    opinions: Mapped[List["Opinion"]] = relationship(
        "Opinion", back_populates="blogger", cascade="all, delete-orphan"
    )
