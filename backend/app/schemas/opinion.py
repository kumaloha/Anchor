from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.opinion import OpinionStatusEnum, OpinionTypeEnum
from app.models.opinion_detail import (
    AssumptionLevelEnum,
    PredictionVerificationStatusEnum,
    SentimentEnum,
)
from app.models.verification import VerificationResultEnum


# ---- Verification schemas ----

class VerificationRecordOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    opinion_id: int
    check_type: str
    result: VerificationResultEnum
    evidence_text: Optional[str] = None
    source_url: Optional[str] = None
    authoritative: bool
    checked_at: datetime


# ---- Detail schemas ----

class PredictionDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    prediction_summary: str
    deadline: Optional[date] = None
    verification_status: PredictionVerificationStatusEnum
    evidence_links: Optional[List[str]] = None
    authoritative_sources: Optional[List[str]] = None
    last_checked_at: Optional[datetime] = None


class HistoryDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    claim_summary: str
    is_complete: bool
    assumption_level: AssumptionLevelEnum
    has_assumptions: bool
    assumption_list: Optional[List[str]] = None
    can_verify: bool
    verification_notes: Optional[str] = None


class AdviceDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    advice_summary: str
    basis: Optional[str] = None
    rarity_score: int
    importance_score: int
    source_credibility: Optional[str] = None
    action_items: Optional[List[str]] = None


class CommentaryDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sentiment: SentimentEnum
    target_subject: Optional[str] = None
    public_opinion_summary: Optional[str] = None
    followup_opinions: Optional[List[str]] = None
    last_tracked_at: Optional[datetime] = None


# ---- Opinion schemas ----

class OpinionCreate(BaseModel):
    blogger_id: int
    raw_content_id: Optional[int] = None
    text: str
    abstract_level: int = Field(default=1, ge=1, le=3)
    opinion_type: OpinionTypeEnum
    importance: int = Field(default=3, ge=1, le=5)
    influence: int = Field(default=3, ge=1, le=5)
    domain_tags: Optional[List[str]] = None
    topic_tags: Optional[List[str]] = None
    language: Optional[str] = None
    source_quote: Optional[str] = None


class OpinionUpdate(BaseModel):
    text: Optional[str] = None
    abstract_level: Optional[int] = Field(default=None, ge=1, le=3)
    opinion_type: Optional[OpinionTypeEnum] = None
    status: Optional[OpinionStatusEnum] = None
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    influence: Optional[int] = Field(default=None, ge=1, le=5)
    domain_tags: Optional[List[str]] = None
    topic_tags: Optional[List[str]] = None
    language: Optional[str] = None
    source_quote: Optional[str] = None


class OpinionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    blogger_id: int
    raw_content_id: Optional[int] = None
    text: str
    abstract_level: int
    opinion_type: OpinionTypeEnum
    status: OpinionStatusEnum
    importance: int
    influence: int
    domain_tags: Optional[List[str]] = None
    topic_tags: Optional[List[str]] = None
    language: Optional[str] = None
    source_quote: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class OpinionDetailOut(OpinionOut):
    """Opinion with all type-specific detail and verifications."""

    prediction_detail: Optional[PredictionDetailOut] = None
    history_detail: Optional[HistoryDetailOut] = None
    advice_detail: Optional[AdviceDetailOut] = None
    commentary_detail: Optional[CommentaryDetailOut] = None
    verifications: List[VerificationRecordOut] = []


# ---- Ingest schemas ----

class ManualIngestRequest(BaseModel):
    blogger_id: int
    text: str
    source_quote: Optional[str] = None
    language: Optional[str] = None
    domain_tags: Optional[List[str]] = None
    topic_tags: Optional[List[str]] = None


class URLIngestRequest(BaseModel):
    url: str
    blogger_id: int


# ---- Tracking summary schema ----

class TrackingSummary(BaseModel):
    total_opinions: int
    by_type: dict
    by_status: dict
    pending_verifications: int
    verified_true: int
    verified_false: int
