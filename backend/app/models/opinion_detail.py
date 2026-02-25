import enum
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
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
    from app.models.opinion import Opinion


# ---------- Type A: Prediction ----------

class PredictionVerificationStatusEnum(str, enum.Enum):
    pending = "pending"
    verified_true = "verified_true"
    verified_false = "verified_false"
    expired = "expired"


class PredictionDetail(Base):
    __tablename__ = "prediction_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opinion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    prediction_summary: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    verification_status: Mapped[PredictionVerificationStatusEnum] = mapped_column(
        Enum(PredictionVerificationStatusEnum, name="prediction_verification_status_enum"),
        default=PredictionVerificationStatusEnum.pending,
        nullable=False,
    )
    evidence_links: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    authoritative_sources: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    opinion: Mapped["Opinion"] = relationship("Opinion", back_populates="prediction_detail")


# ---------- Type B: History ----------

class AssumptionLevelEnum(str, enum.Enum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class HistoryDetail(Base):
    __tablename__ = "history_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opinion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    claim_summary: Mapped[str] = mapped_column(Text, nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assumption_level: Mapped[AssumptionLevelEnum] = mapped_column(
        Enum(AssumptionLevelEnum, name="assumption_level_enum"),
        default=AssumptionLevelEnum.none,
        nullable=False,
    )
    has_assumptions: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assumption_list: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    can_verify: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    opinion: Mapped["Opinion"] = relationship("Opinion", back_populates="history_detail")


# ---------- Type C: Advice ----------

class AdviceDetail(Base):
    __tablename__ = "advice_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opinion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    advice_summary: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rarity_score: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    importance_score: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    source_credibility: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_items: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    opinion: Mapped["Opinion"] = relationship("Opinion", back_populates="advice_detail")


# ---------- Type D: Commentary ----------

class SentimentEnum(str, enum.Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"
    mixed = "mixed"


class CommentaryDetail(Base):
    __tablename__ = "commentary_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opinion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    sentiment: Mapped[SentimentEnum] = mapped_column(
        Enum(SentimentEnum, name="sentiment_enum"),
        default=SentimentEnum.neutral,
        nullable=False,
    )
    target_subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    public_opinion_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followup_opinions: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    last_tracked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    opinion: Mapped["Opinion"] = relationship("Opinion", back_populates="commentary_detail")
