import logging
from datetime import date
from typing import List, Literal, Optional, Union

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# ---------- Structured output models for classification ----------

class PredictionAttributes(BaseModel):
    opinion_type: Literal["prediction"] = "prediction"
    prediction_summary: str = Field(description="Concise restatement of the prediction")
    deadline: Optional[str] = Field(
        default=None,
        description="Expected deadline date in ISO 8601 format (YYYY-MM-DD) if mentioned, else null"
    )


class HistoryAttributes(BaseModel):
    opinion_type: Literal["history"] = "history"
    claim_summary: str = Field(description="Concise restatement of the historical claim")
    is_complete: bool = Field(description="True if the claim is informationally complete (no missing key facts)")
    assumption_level: Literal["none", "low", "medium", "high"] = Field(
        description="Level of unverified assumptions in the claim"
    )
    has_assumptions: bool = Field(description="True if the claim contains assumptions")
    assumption_list: List[str] = Field(
        default_factory=list,
        description="List of specific assumptions made in the claim"
    )


class AdviceAttributes(BaseModel):
    opinion_type: Literal["advice"] = "advice"
    advice_summary: str = Field(description="Concise restatement of the advice")
    basis: str = Field(description="The reasoning or evidence the author gives for this advice")
    rarity_score: int = Field(
        description="How rare/unique is this advice vs. common knowledge? 1=very common, 5=very rare",
        ge=1, le=5
    )
    importance_score: int = Field(
        description="How actionable and impactful is this advice? 1=low, 5=high",
        ge=1, le=5
    )


class CommentaryAttributes(BaseModel):
    opinion_type: Literal["commentary"] = "commentary"
    sentiment: Literal["positive", "negative", "neutral", "mixed"] = Field(
        description="Overall sentiment of the commentary"
    )
    target_subject: str = Field(
        description="The person, entity, event, or topic being commented on"
    )


class ClassificationResult(BaseModel):
    attributes: Union[PredictionAttributes, HistoryAttributes, AdviceAttributes, CommentaryAttributes] = Field(
        discriminator="opinion_type"
    )


CLASSIFICATION_SYSTEM_PROMPT = """You are an expert opinion classifier. Given an opinion statement, classify it into exactly one of the following four types and extract the relevant attributes:

1. **prediction** — A forward-looking claim about what will happen in the future. Includes forecasts, projections, bets on future events.
   Required: prediction_summary, deadline (if stated, else null)

2. **history** — A claim about past events or historical facts. May be complete and verifiable, or may contain gaps/assumptions.
   Required: claim_summary, is_complete, assumption_level (none/low/medium/high), has_assumptions, assumption_list

3. **advice** — A recommendation, best practice, or prescriptive guidance for action. Includes personal development tips, strategic suggestions.
   Required: advice_summary, basis, rarity_score (1-5), importance_score (1-5)

4. **commentary** — Analysis, critique, or subjective evaluation of a person, entity, or event. Includes opinions on current affairs, reviews, editorials.
   Required: sentiment (positive/negative/neutral/mixed), target_subject

Choose the type that best fits the primary intent of the opinion."""


class Classifier:
    """Uses GPT-4o structured outputs to classify opinions into 4 types."""

    async def classify(self, opinion_text: str) -> Optional[ClassificationResult]:
        """
        Classify an opinion text into one of: prediction, history, advice, commentary.

        Returns a ClassificationResult with type-specific attributes, or None on error.
        """
        if not opinion_text.strip():
            return None

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Classify this opinion:\n\n{opinion_text}",
                    },
                ],
                response_format=ClassificationResult,
                temperature=0.1,
            )
            result = response.choices[0].message.parsed
            return result
        except Exception as exc:
            logger.error("Classifier.classify failed for opinion '%s...': %s", opinion_text[:80], exc)
            return None

    def parse_deadline(self, deadline_str: Optional[str]) -> Optional[date]:
        """Parse an ISO 8601 date string into a date object, returning None if invalid."""
        if not deadline_str:
            return None
        try:
            return date.fromisoformat(deadline_str)
        except ValueError:
            # Try partial formats like "2025-Q3" or year-only "2026"
            if len(deadline_str) == 4 and deadline_str.isdigit():
                try:
                    return date(int(deadline_str), 12, 31)
                except ValueError:
                    pass
            logger.warning("Classifier: could not parse deadline '%s'", deadline_str)
            return None
