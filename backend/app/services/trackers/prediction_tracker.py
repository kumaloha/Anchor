import logging
from datetime import datetime, timezone
from typing import List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.opinion import Opinion, OpinionStatusEnum
from app.models.opinion_detail import PredictionDetail, PredictionVerificationStatusEnum
from app.models.verification import VerificationRecord, VerificationResultEnum
from app.services.trackers.base import BaseTracker

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class PredictionVerificationLLMResult(BaseModel):
    verified: bool = Field(description="True if the prediction has come true, False if it has been falsified")
    inconclusive: bool = Field(description="True if there is not enough public information to determine the outcome")
    evidence_summary: str = Field(description="A concise summary of the evidence found")
    authoritative_sources: List[str] = Field(
        default_factory=list,
        description="Names of authoritative sources referenced (e.g., 'Reuters', 'official government announcement')"
    )
    confidence: int = Field(description="Confidence level 1-5 in this verification", ge=1, le=5)


PREDICTION_TRACKER_SYSTEM = """You are a fact-checking expert specializing in verifying whether predictions have come true.

Given a prediction made by a public commentator, use your knowledge to:
1. Check if the predicted event or outcome has occurred.
2. Identify authoritative sources (government reports, major news organizations, company filings) that confirm or refute it.
3. Summarize the evidence concisely.

Be honest about uncertainty — if you don't have reliable information, mark it as inconclusive.
Focus on publicly verifiable outcomes only."""


class PredictionTracker(BaseTracker):
    """Tracks and verifies prediction-type opinions using LLM knowledge."""

    def supports_type(self, opinion_type: str) -> bool:
        return opinion_type == "prediction"

    async def track(self, opinion: Opinion, session: AsyncSession) -> None:
        if not opinion.prediction_detail:
            logger.warning(
                "PredictionTracker: opinion %d has no prediction_detail, skipping", opinion.id
            )
            return

        detail: PredictionDetail = opinion.prediction_detail
        now = datetime.now(timezone.utc)

        # Build query for LLM
        deadline_str = detail.deadline.isoformat() if detail.deadline else "no specified deadline"
        prompt = (
            f"Prediction to verify: {detail.prediction_summary}\n"
            f"Stated deadline: {deadline_str}\n"
            f"Made by: {opinion.blogger.name if opinion.blogger else 'unknown commentator'}"
        )

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": PREDICTION_TRACKER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format=PredictionVerificationLLMResult,
                temperature=0.1,
            )
            llm_result = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("PredictionTracker: LLM call failed for opinion %d: %s", opinion.id, exc)
            return

        if llm_result is None:
            return

        # Determine verification status
        if llm_result.inconclusive:
            verification_status = PredictionVerificationStatusEnum.pending
            result = VerificationResultEnum.inconclusive
        elif llm_result.verified:
            verification_status = PredictionVerificationStatusEnum.verified_true
            result = VerificationResultEnum.supports
        else:
            verification_status = PredictionVerificationStatusEnum.verified_false
            result = VerificationResultEnum.refutes

        # Update PredictionDetail
        detail.verification_status = verification_status
        detail.last_checked_at = now
        existing_sources = detail.authoritative_sources or []
        new_sources = [s for s in llm_result.authoritative_sources if s not in existing_sources]
        detail.authoritative_sources = existing_sources + new_sources
        session.add(detail)

        # Create VerificationRecord
        record = VerificationRecord(
            opinion_id=opinion.id,
            check_type="prediction_llm_check",
            result=result,
            evidence_text=llm_result.evidence_summary,
            source_url=None,
            authoritative=llm_result.confidence >= 4,
            checked_at=now,
        )
        session.add(record)

        # Update opinion status
        if verification_status == PredictionVerificationStatusEnum.verified_true:
            opinion.status = OpinionStatusEnum.verified
        elif verification_status == PredictionVerificationStatusEnum.verified_false:
            opinion.status = OpinionStatusEnum.refuted
        else:
            opinion.status = OpinionStatusEnum.tracking
        session.add(opinion)

        logger.info(
            "PredictionTracker: opinion %d → status=%s, verification=%s",
            opinion.id,
            opinion.status,
            verification_status,
        )
