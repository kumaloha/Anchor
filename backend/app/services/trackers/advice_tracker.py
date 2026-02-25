import logging
from datetime import datetime, timezone
from typing import List

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.opinion import Opinion, OpinionStatusEnum
from app.models.opinion_detail import AdviceDetail
from app.models.verification import VerificationRecord, VerificationResultEnum
from app.services.trackers.base import BaseTracker

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class AdviceTrackingLLMResult(BaseModel):
    basis_assessment: str = Field(
        description="Assessment of the reasoning/evidence the author uses to justify the advice"
    )
    rarity_score: int = Field(
        description="How rare/unique is this advice vs. common knowledge? 1=very common, 5=very rare",
        ge=1, le=5
    )
    importance_score: int = Field(
        description="How actionable and impactful is this advice? 1=low, 5=high",
        ge=1, le=5
    )
    source_credibility: str = Field(
        description="Assessment of the source's credibility for giving this advice"
    )
    action_items: List[str] = Field(
        default_factory=list,
        description="Concrete action items implied by the advice"
    )
    overall_assessment: str = Field(
        description="Overall evaluation of the advice quality and value"
    )


ADVICE_TRACKER_SYSTEM = """You are an expert adviser evaluator.

Given a piece of advice from a public commentator, assess:
1. The basis: Is the reasoning/evidence behind the advice sound and well-founded?
2. Rarity: How unique is this advice vs. widely-known common knowledge? (1=cliché, 5=genuinely rare insight)
3. Importance: How actionable and impactful would following this advice be? (1=trivial, 5=potentially life/business changing)
4. Source credibility: How credible is this source for giving this advice in their domain?
5. Action items: What concrete steps does this advice imply?
6. Overall assessment: A concise evaluation of the advice's overall value.

Be critical and honest. Common platitudes should receive low rarity scores."""


class AdviceTracker(BaseTracker):
    """Tracks and evaluates advice-type opinions."""

    def supports_type(self, opinion_type: str) -> bool:
        return opinion_type == "advice"

    async def track(self, opinion: Opinion, session: AsyncSession) -> None:
        if not opinion.advice_detail:
            logger.warning(
                "AdviceTracker: opinion %d has no advice_detail, skipping", opinion.id
            )
            return

        detail: AdviceDetail = opinion.advice_detail
        now = datetime.now(timezone.utc)

        blogger_context = ""
        if opinion.blogger:
            blogger_context = f"Advice from: {opinion.blogger.name} (platform: {opinion.blogger.platform.value})"

        prompt = (
            f"{blogger_context}\n\n"
            f"Advice: {detail.advice_summary}\n"
            f"Stated basis: {detail.basis or 'not provided'}"
        )

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": ADVICE_TRACKER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format=AdviceTrackingLLMResult,
                temperature=0.2,
            )
            llm_result = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("AdviceTracker: LLM call failed for opinion %d: %s", opinion.id, exc)
            return

        if llm_result is None:
            return

        # Update AdviceDetail
        detail.basis = llm_result.basis_assessment
        detail.rarity_score = llm_result.rarity_score
        detail.importance_score = llm_result.importance_score
        detail.source_credibility = llm_result.source_credibility
        detail.action_items = llm_result.action_items
        session.add(detail)

        # Create VerificationRecord
        record = VerificationRecord(
            opinion_id=opinion.id,
            check_type="advice_evaluation",
            result=VerificationResultEnum.supports,
            evidence_text=llm_result.overall_assessment,
            source_url=None,
            authoritative=False,
            checked_at=now,
        )
        session.add(record)

        # Update opinion status to tracking (advice doesn't get "verified" per se)
        opinion.status = OpinionStatusEnum.tracking
        # Boost importance if high importance_score
        if llm_result.importance_score >= 4:
            opinion.importance = max(opinion.importance, llm_result.importance_score)
        session.add(opinion)

        logger.info(
            "AdviceTracker: opinion %d → rarity=%d, importance=%d",
            opinion.id,
            llm_result.rarity_score,
            llm_result.importance_score,
        )
