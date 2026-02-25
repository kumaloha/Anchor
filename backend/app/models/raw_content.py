import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.blogger import Blogger
    from app.models.opinion import Opinion


class ContentTypeEnum(str, enum.Enum):
    text = "text"
    video = "video"


class RawContent(Base):
    __tablename__ = "raw_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    blogger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    content_type: Mapped[ContentTypeEnum] = mapped_column(
        Enum(ContentTypeEnum, name="content_type_enum"), nullable=False
    )
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    blogger: Mapped["Blogger"] = relationship("Blogger", back_populates="raw_contents")
    opinions: Mapped[List["Opinion"]] = relationship(
        "Opinion", back_populates="raw_content"
    )
