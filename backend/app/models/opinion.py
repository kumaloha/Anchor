import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.blogger import Blogger
    from app.models.raw_content import RawContent
    from app.models.opinion_detail import (
        PredictionDetail,
        HistoryDetail,
        AdviceDetail,
        CommentaryDetail,
    )
    from app.models.verification import VerificationRecord


class OpinionTypeEnum(str, enum.Enum):
    prediction = "prediction"
    history = "history"
    advice = "advice"
    commentary = "commentary"


class OpinionStatusEnum(str, enum.Enum):
    pending = "pending"
    tracking = "tracking"
    verified = "verified"
    refuted = "refuted"
    expired = "expired"
    closed = "closed"


class Opinion(Base):
    __tablename__ = "opinions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    blogger_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_content_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("raw_contents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    abstract_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    opinion_type: Mapped[OpinionTypeEnum] = mapped_column(
        Enum(OpinionTypeEnum, name="opinion_type_enum"), nullable=False, index=True
    )
    status: Mapped[OpinionStatusEnum] = mapped_column(
        Enum(OpinionStatusEnum, name="opinion_status_enum"),
        default=OpinionStatusEnum.pending,
        nullable=False,
        index=True,
    )
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    influence: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    domain_tags: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    topic_tags: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    blogger: Mapped["Blogger"] = relationship("Blogger", back_populates="opinions")
    raw_content: Mapped[Optional["RawContent"]] = relationship(
        "RawContent", back_populates="opinions"
    )
    verifications: Mapped[List["VerificationRecord"]] = relationship(
        "VerificationRecord", back_populates="opinion", cascade="all, delete-orphan"
    )
    prediction_detail: Mapped[Optional["PredictionDetail"]] = relationship(
        "PredictionDetail", back_populates="opinion", uselist=False, cascade="all, delete-orphan"
    )
    history_detail: Mapped[Optional["HistoryDetail"]] = relationship(
        "HistoryDetail", back_populates="opinion", uselist=False, cascade="all, delete-orphan"
    )
    advice_detail: Mapped[Optional["AdviceDetail"]] = relationship(
        "AdviceDetail", back_populates="opinion", uselist=False, cascade="all, delete-orphan"
    )
    commentary_detail: Mapped[Optional["CommentaryDetail"]] = relationship(
        "CommentaryDetail", back_populates="opinion", uselist=False, cascade="all, delete-orphan"
    )
