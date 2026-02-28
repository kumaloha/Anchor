"""
情绪快照追踪器
==============
对情绪观点做时序快照：
  1. 检查 posted_at 在 72h 内，否则返回 None
  2. 用 fetch_comments() 抓取最新评论
  3. 调用 Claude 分析（复用 opinion_analyzer 的 prompt 和解析逻辑）
  4. 写 SentimentSnapshot（轻量：不存 raw_comments）
"""

from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

import anthropic
from anchor.classifier.opinion_analyzer import (
    _SYSTEM_PROMPT,
    _build_opinion_prompt,
    _parse_result,
)
from anchor.collector.comment_collector import RawComment, fetch_comments
from anchor.config import settings
from anchor.models import Sentiment, SentimentSnapshot, _utcnow

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 3000
_WINDOW_HOURS = 72
_MAX_COMMENTS = 50


class SentimentTracker:
    """对单个情绪观点执行舆情快照。"""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def snapshot(
        self, sentiment: Sentiment, session: AsyncSession
    ) -> SentimentSnapshot | None:
        """对情绪执行一次快照，写入 SentimentSnapshot。

        超出 72h 窗口返回 None。
        """
        now = _utcnow()
        posted_at = sentiment.posted_at
        # posted_at 可能含时区信息，统一处理
        if posted_at.tzinfo is not None:
            from datetime import timezone
            now_tz = __import__("datetime").datetime.now(timezone.utc)
            age = now_tz - posted_at
        else:
            age = now - posted_at

        if age > timedelta(hours=_WINDOW_HOURS):
            logger.debug(
                f"[SentimentTracker] sentiment id={sentiment.id} posted {age} ago, "
                f"outside {_WINDOW_HOURS}h window, skip"
            )
            return None

        # 抓取评论（来源帖子即情绪帖子本身）
        comments: list[RawComment] = await fetch_comments(
            sentiment.source_platform,
            _extract_external_id(sentiment.source_url),
            _MAX_COMMENTS,
        )

        if not comments:
            logger.info(
                f"[SentimentTracker] No comments fetched for sentiment id={sentiment.id}"
            )
            # 仍记录一条空快照，保持时序完整性
            snap = SentimentSnapshot(
                sentiment_id=sentiment.id,
                relevant_comments_count=0,
                snapshotted_at=now,
            )
            session.add(snap)
            await session.flush()
            return snap

        # 调用 Claude 分析
        result = await self._call_claude(sentiment, comments)
        if result is None:
            logger.warning(
                f"[SentimentTracker] Claude analysis failed for sentiment id={sentiment.id}"
            )
            return None

        snap = SentimentSnapshot(
            sentiment_id=sentiment.id,
            resonance_level=result.resonance_level,
            dominant_emotion=result.dominant_emotion,
            emotion_diversity=result.emotion_diversity,
            relevant_comments_count=result.relevant_count,
            relevance_ratio=result.relevance_ratio,
            summary=result.summary,
            snapshotted_at=now,
        )
        session.add(snap)
        await session.flush()

        logger.info(
            f"[SentimentTracker] snapshot done for sentiment id={sentiment.id}: "
            f"resonance={result.resonance_level}, relevant={result.relevant_count}/{result.total_analyzed}"
        )
        return snap

    async def _call_claude(self, sentiment: Sentiment, comments: list[RawComment]):
        user_message = _build_opinion_prompt(sentiment, comments)
        try:
            response = await self._client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text
            return _parse_result(raw)
        except anthropic.APIError as exc:
            logger.error(f"[SentimentTracker] Claude API error: {exc}")
            return None


def _extract_external_id(source_url: str) -> str:
    """从 URL 中提取最后一段作为 external_id（粗略实现）。"""
    parts = source_url.rstrip("/").split("/")
    return parts[-1] if parts else source_url
