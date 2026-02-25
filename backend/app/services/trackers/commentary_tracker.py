import logging
from datetime import datetime, timezone
from typing import List, Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.opinion import Opinion, OpinionStatusEnum
from app.models.opinion_detail import CommentaryDetail, SentimentEnum
from app.models.verification import VerificationRecord, VerificationResultEnum
from app.services.trackers.base import BaseTracker

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


class CommentaryTrackingLLMResult(BaseModel):
    sentiment: Literal["positive", "negative", "neutral", "mixed"] = Field(
        description="Overall sentiment expressed in the commentary"
    )
    target_subject: str = Field(
        description="The person, entity, event, or topic being commented on"
    )
    public_opinion_summary: str = Field(
        description="Summary of the general public discourse and sentiment around this topic"
    )
    followup_opinions: List[str] = Field(
        default_factory=list,
        description="Key viewpoints observed in broader public discourse about this topic"
    )
    context_summary: str = Field(
        description="Brief context about the topic being discussed and its significance"
    )


COMMENTARY_TRACKER_SYSTEM = """You are a public opinion analyst with expertise in media and social commentary.

Given a commentary opinion from a public commentator, track:
1. Confirm the sentiment and target subject of the commentary.
2. Summarize the broader public opinion and discourse around this topic.
3. List key viewpoints seen in public discussion (e.g., supporters, critics, neutral observers).
4. Provide brief context about the topic's significance.

Draw on your knowledge of public discourse, news media, and online discussion patterns."""


class CommentaryTracker(BaseTracker):
    """Tracks public sentiment and discourse around commentary-type opinions."""

    def supports_type(self, opinion_type: str) -> bool:
        return opinion_type == "commentary"

    async def track(self, opinion: Opinion, session: AsyncSession) -> None:
        if not opinion.commentary_detail:
            logger.warning(
                "CommentaryTracker: opinion %d has no commentary_detail, skipping", opinion.id
            )
            return

        detail: CommentaryDetail = opinion.commentary_detail
        now = datetime.now(timezone.utc)

        blogger_context = ""
        if opinion.blogger:
            blogger_context = f"Commentary by: {opinion.blogger.name}"

        prompt = (
            f"{blogger_context}\n\n"
            f"Commentary: {opinion.text}\n"
            f"Target subject: {detail.target_subject or 'unspecified'}\n"
            f"Current sentiment assessment: {detail.sentiment.value}"
        )

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": COMMENTARY_TRACKER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format=CommentaryTrackingLLMResult,
                temperature=0.3,
            )
            llm_result = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("CommentaryTracker: LLM call failed for opinion %d: %s", opinion.id, exc)
            return

        if llm_result is None:
            return

        # Map sentiment string to enum
        sentiment_map = {
            "positive": SentimentEnum.positive,
            "negative": SentimentEnum.negative,
            "neutral": SentimentEnum.neutral,
            "mixed": SentimentEnum.mixed,
        }
        sentiment_enum = sentiment_map.get(llm_result.sentiment, SentimentEnum.neutral)

        # Update CommentaryDetail
        detail.sentiment = sentiment_enum
        detail.target_subject = llm_result.target_subject
        detail.public_opinion_summary = llm_result.public_opinion_summary
        detail.followup_opinions = llm_result.followup_opinions
        detail.last_tracked_at = now
        session.add(detail)

        # Create VerificationRecord
        record = VerificationRecord(
            opinion_id=opinion.id,
            check_type="commentary_public_sentiment",
            result=VerificationResultEnum.inconclusive,
            evidence_text=llm_result.context_summary,
            source_url=None,
            authoritative=False,
            checked_at=now,
        )
        session.add(record)

        # Update opinion status
        opinion.status = OpinionStatusEnum.tracking
        session.add(opinion)

        logger.info(
            "CommentaryTracker: opinion %d → sentiment=%s, target='%s'",
            opinion.id,
            sentiment_enum.value,
            llm_result.target_subject,
        )
