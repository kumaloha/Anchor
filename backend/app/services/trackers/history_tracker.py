import logging
from datetime import datetime, timezone
from typing import List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.opinion import Opinion, OpinionStatusEnum
from app.models.opinion_detail import AssumptionLevelEnum, HistoryDetail
from app.models.verification import VerificationRecord, VerificationResultEnum
from app.services.trackers.base import BaseTracker

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class HistoryVerificationLLMResult(BaseModel):
    is_accurate: bool = Field(description="True if the historical claim is accurate based on authoritative sources")
    is_complete: bool = Field(description="True if the claim contains all key relevant information without notable omissions")
    has_assumptions: bool = Field(description="True if the claim contains unverified assumptions")
    assumption_level: str = Field(description="none, low, medium, or high — degree of assumptions present")
    assumption_list: List[str] = Field(default_factory=list, description="Specific assumptions identified")
    can_verify: bool = Field(description="True if the claim is complete and has low/no assumptions — i.e., it's verifiable")
    evidence_summary: str = Field(description="Summary of evidence supporting or refuting the claim")
    inconclusive: bool = Field(description="True if there is insufficient public information to verify")


HISTORY_TRACKER_SYSTEM = """You are a historical fact-checker and research expert.

Given a historical claim made by a public commentator, assess:
1. Accuracy: Is the claim correct based on established historical record?
2. Completeness: Does the claim contain all key relevant facts, or does it omit important context?
3. Assumptions: Does the claim rely on unverified assumptions? How significant are they?
4. Verifiability: Can the claim be definitively verified (complete + minimal assumptions)?

Mark as inconclusive only if there truly isn't enough reliable public information.
Be precise and reference authoritative historical sources when possible."""


class HistoryTracker(BaseTracker):
    """Tracks and verifies historical claim opinions."""

    def supports_type(self, opinion_type: str) -> bool:
        return opinion_type == "history"

    async def track(self, opinion: Opinion, session: AsyncSession) -> None:
        if not opinion.history_detail:
            logger.warning(
                "HistoryTracker: opinion %d has no history_detail, skipping", opinion.id
            )
            return

        detail: HistoryDetail = opinion.history_detail
        now = datetime.now(timezone.utc)

        # If assumption_level is already high, close immediately as unverifiable
        if detail.assumption_level == AssumptionLevelEnum.high:
            opinion.status = OpinionStatusEnum.closed
            detail.can_verify = False
            detail.verification_notes = "Marked as closed: assumption level is too high to verify."
            session.add(detail)
            session.add(opinion)
            logger.info("HistoryTracker: opinion %d closed (high assumptions)", opinion.id)
            return

        prompt = (
            f"Historical claim to verify: {detail.claim_summary}\n\n"
            f"Current assessment: is_complete={detail.is_complete}, "
            f"assumption_level={detail.assumption_level.value}, "
            f"has_assumptions={detail.has_assumptions}"
        )

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": HISTORY_TRACKER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format=HistoryVerificationLLMResult,
                temperature=0.1,
            )
            llm_result = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("HistoryTracker: LLM call failed for opinion %d: %s", opinion.id, exc)
            return

        if llm_result is None:
            return

        # Map assumption_level string to enum
        assumption_level_map = {
            "none": AssumptionLevelEnum.none,
            "low": AssumptionLevelEnum.low,
            "medium": AssumptionLevelEnum.medium,
            "high": AssumptionLevelEnum.high,
        }
        assumption_level_enum = assumption_level_map.get(
            llm_result.assumption_level.lower(), AssumptionLevelEnum.medium
        )

        # If LLM finds high assumptions, close
        if assumption_level_enum == AssumptionLevelEnum.high:
            opinion.status = OpinionStatusEnum.closed
            detail.can_verify = False
            detail.assumption_level = assumption_level_enum
            detail.has_assumptions = llm_result.has_assumptions
            detail.assumption_list = llm_result.assumption_list
            detail.verification_notes = f"Closed (high assumptions): {llm_result.evidence_summary}"
            session.add(detail)
            session.add(opinion)
            return

        # Update detail
        detail.is_complete = llm_result.is_complete
        detail.assumption_level = assumption_level_enum
        detail.has_assumptions = llm_result.has_assumptions
        detail.assumption_list = llm_result.assumption_list
        detail.can_verify = llm_result.can_verify
        detail.verification_notes = llm_result.evidence_summary
        session.add(detail)

        # Determine verification result
        if llm_result.inconclusive:
            result = VerificationResultEnum.inconclusive
            opinion.status = OpinionStatusEnum.tracking
        elif llm_result.is_accurate:
            result = VerificationResultEnum.supports
            opinion.status = OpinionStatusEnum.verified
        else:
            result = VerificationResultEnum.refutes
            opinion.status = OpinionStatusEnum.refuted
        session.add(opinion)

        # Create VerificationRecord
        record = VerificationRecord(
            opinion_id=opinion.id,
            check_type="history_llm_check",
            result=result,
            evidence_text=llm_result.evidence_summary,
            source_url=None,
            authoritative=llm_result.can_verify,
            checked_at=now,
        )
        session.add(record)

        logger.info(
            "HistoryTracker: opinion %d → status=%s, can_verify=%s",
            opinion.id,
            opinion.status,
            llm_result.can_verify,
        )
