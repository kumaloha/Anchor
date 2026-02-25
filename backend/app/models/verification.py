import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

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
    from app.models.opinion import Opinion


class VerificationResultEnum(str, enum.Enum):
    supports = "supports"
    refutes = "refutes"
    inconclusive = "inconclusive"
    pending = "pending"


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opinion_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    check_type: Mapped[str] = mapped_column(String(100), nullable=False)
    result: Mapped[VerificationResultEnum] = mapped_column(
        Enum(VerificationResultEnum, name="verification_result_enum"),
        default=VerificationResultEnum.pending,
        nullable=False,
    )
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    authoritative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    opinion: Mapped["Opinion"] = relationship("Opinion", back_populates="verifications")
