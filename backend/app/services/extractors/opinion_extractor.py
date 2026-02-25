import logging
from typing import List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


# ---------- Pydantic structured output models ----------

class ExtractedOpinion(BaseModel):
    text: str = Field(description="The opinion statement, clearly expressed")
    source_quote: str = Field(description="The exact quote from the source text that expresses this opinion")
    abstract_level: int = Field(
        description="Abstraction level: 1=raw/verbatim, 2=paraphrased summary, 3=core theme",
        ge=1, le=3
    )
    importance: int = Field(
        description="Initial importance rating from 1 (trivial) to 5 (highly significant)",
        ge=1, le=5
    )
    language: str = Field(description="ISO 639-1 language code of the opinion (e.g. 'en', 'zh', 'ja')")


class ExtractionResult(BaseModel):
    opinions: List[ExtractedOpinion] = Field(
        description="All distinct opinions extracted from the text"
    )


class AbstractionResult(BaseModel):
    summary: str = Field(description="A higher-level synthesis of the given opinions into a core theme or viewpoint")
    abstract_level: int = Field(default=3, ge=1, le=3)
    importance: int = Field(ge=1, le=5)
    key_themes: List[str] = Field(description="Key themes covered by these opinions")


EXTRACTION_SYSTEM_PROMPT = """You are an expert opinion analyst. Your task is to read text from social media posts, articles, or video transcripts and extract all distinct opinions expressed by the author.

Guidelines:
- An opinion is a subjective viewpoint, belief, prediction, or recommendation — not a neutral fact.
- Extract each distinct opinion separately, even if multiple opinions appear in a single post.
- For each opinion, provide:
  * text: A clear, standalone statement of the opinion.
  * source_quote: The exact original phrase(s) that express this opinion.
  * abstract_level: 1 if it closely mirrors the source wording, 2 if summarized, 3 if it's a high-level theme.
  * importance: Rate 1-5 based on how significant or impactful the opinion seems (consider reach, boldness, consequence).
  * language: ISO 639-1 code of the language used.
- Ignore purely factual statements (e.g., "The meeting happened on Monday").
- If no opinions are present, return an empty list."""

ABSTRACTION_SYSTEM_PROMPT = """You are an expert opinion synthesizer. Given a list of related opinions, produce a concise higher-level synthesis that captures the core viewpoint or theme.

Guidelines:
- Produce a single, clear synthesis statement.
- Identify the key themes.
- Rate the overall importance 1-5.
- Set abstract_level to 3 (core theme)."""


class OpinionExtractor:
    """Uses GPT-4o structured outputs to extract opinions from text."""

    async def extract(self, text: str, blogger_context: Optional[str] = None) -> List[ExtractedOpinion]:
        """
        Extract all distinct opinions from raw text or transcript.

        Args:
            text: The raw text/transcript to analyze.
            blogger_context: Optional context about the blogger (e.g., their domain).

        Returns:
            List of ExtractedOpinion objects.
        """
        if not text.strip():
            return []

        user_content = text
        if blogger_context:
            user_content = f"[Author context: {blogger_context}]\n\n{text}"

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=ExtractionResult,
                temperature=0.2,
            )
            result = response.choices[0].message.parsed
            if result is None:
                logger.warning("OpinionExtractor: model returned no parsed result")
                return []
            return result.opinions
        except Exception as exc:
            logger.error("OpinionExtractor.extract failed: %s", exc)
            return []

    async def abstract(self, opinions: List[str]) -> Optional[AbstractionResult]:
        """
        Given a list of opinion texts, produce a higher-level abstraction/synthesis.

        Args:
            opinions: List of opinion text strings.

        Returns:
            An AbstractionResult or None on failure.
        """
        if not opinions:
            return None

        numbered = "\n".join(f"{i+1}. {op}" for i, op in enumerate(opinions))
        user_content = f"Here are {len(opinions)} related opinions:\n\n{numbered}"

        try:
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": ABSTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=AbstractionResult,
                temperature=0.2,
            )
            result = response.choices[0].message.parsed
            return result
        except Exception as exc:
            logger.error("OpinionExtractor.abstract failed: %s", exc)
            return None
