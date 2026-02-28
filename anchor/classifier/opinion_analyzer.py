"""
舆情分析器
==========
流程：
  1. 接收 Sentiment + 关联帖子信息
  2. 调用 CommentCollector 抓取评论
  3. 调用 Claude API 分析评论相关性和情绪模式
  4. 写入 PublicOpinion，更新 Sentiment.resonance_score

公共入口：OpinionAnalyzer.analyze(sentiment, source_posts, session)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.classifier.schemas import ExtractedPublicOpinion
from anchor.collector.comment_collector import RawComment, fetch_comments
from anchor.llm_client import chat_completion
from anchor.models import (
    PublicOpinion,
    PublicOpinionStatus,
    ResonanceLevel,
    Sentiment,
    _utcnow,
)

_MAX_TOKENS = 3000
_DEFAULT_MAX_COMMENTS = 50


@dataclass
class SourcePostRef:
    """指向采集评论的来源帖子"""
    platform: str
    external_id: str
    url: str


class OpinionAnalyzer:
    """舆情分析器

    Usage:
        analyzer = OpinionAnalyzer()
        public_opinion = await analyzer.analyze(sentiment, source_posts, session)
    """

    def __init__(self, max_comments_per_post: int = _DEFAULT_MAX_COMMENTS) -> None:
        self._max_comments = max_comments_per_post

    async def analyze(
        self,
        sentiment: Sentiment,
        source_posts: list[SourcePostRef],
        session: AsyncSession,
    ) -> PublicOpinion | None:
        """对情绪观点执行完整舆情分析，写入 DB 并更新 Sentiment.resonance_score。

        Args:
            sentiment:    已入库的 Sentiment 对象（含 id）
            source_posts: 需要抓取评论的帖子列表（通常是触发该情绪的帖子）
            session:      数据库 session
        """
        if not sentiment.id:
            logger.error("[OpinionAnalyzer] Sentiment has no id, cannot proceed")
            return None

        # 创建 PublicOpinion 占位记录
        source_json = json.dumps(
            [{"platform": p.platform, "external_id": p.external_id, "url": p.url}
             for p in source_posts],
            ensure_ascii=False,
        )
        pub_opinion = PublicOpinion(
            sentiment_id=sentiment.id,
            source_posts_json=source_json,
            status=PublicOpinionStatus.PENDING,
        )
        session.add(pub_opinion)
        await session.flush()

        # 抓取评论
        all_comments: list[RawComment] = []
        for post_ref in source_posts:
            comments = await fetch_comments(
                post_ref.platform, post_ref.external_id, self._max_comments
            )
            all_comments.extend(comments)

        if not all_comments:
            logger.info(
                f"[OpinionAnalyzer] No comments fetched for sentiment id={sentiment.id}"
            )
            pub_opinion.status = PublicOpinionStatus.FAILED
            pub_opinion.fetch_error = "No comments fetched"
            session.add(pub_opinion)
            await session.commit()
            return pub_opinion

        # 更新采集状态
        pub_opinion.total_comments_fetched = len(all_comments)
        pub_opinion.raw_comments_json = _comments_to_json(all_comments)
        pub_opinion.status = PublicOpinionStatus.FETCHED
        session.add(pub_opinion)
        await session.flush()

        logger.info(
            f"[OpinionAnalyzer] Fetched {len(all_comments)} comments "
            f"for sentiment id={sentiment.id}, sending to Claude"
        )

        # LLM 分析
        result = await self._call_claude(sentiment, all_comments)
        if result is None:
            pub_opinion.status = PublicOpinionStatus.FAILED
            pub_opinion.fetch_error = "Claude analysis failed"
            session.add(pub_opinion)
            await session.commit()
            return pub_opinion

        # 写入分析结果
        pub_opinion.relevant_comments_count = result.relevant_count
        pub_opinion.relevance_ratio = result.relevance_ratio
        pub_opinion.resonance_level = ResonanceLevel(result.resonance_level)
        pub_opinion.dominant_emotion = result.dominant_emotion
        pub_opinion.emotion_diversity = result.emotion_diversity
        pub_opinion.summary = result.summary
        pub_opinion.representative_comments_json = json.dumps(
            result.representative_comments, ensure_ascii=False
        )
        pub_opinion.status = PublicOpinionStatus.ANALYZED
        pub_opinion.analyzed_at = _utcnow()
        session.add(pub_opinion)

        # 回写 Sentiment.resonance_score（按共鸣等级映射）
        resonance_score_map = {
            "high": 0.85,
            "medium": 0.55,
            "low": 0.25,
            "negligible": 0.05,
        }
        sentiment.resonance_score = resonance_score_map.get(result.resonance_level, None)
        sentiment.resonance_note = result.summary
        session.add(sentiment)

        await session.commit()
        logger.info(
            f"[OpinionAnalyzer] Analysis done for sentiment id={sentiment.id}: "
            f"resonance={result.resonance_level}, relevant={result.relevant_count}/{result.total_analyzed}"
        )
        return pub_opinion

    async def _call_claude(
        self, sentiment: Sentiment, comments: list[RawComment]
    ) -> ExtractedPublicOpinion | None:
        """调用 LLM 分析评论与情绪的相关性及整体模式。"""
        user_message = _build_opinion_prompt(sentiment, comments)
        response = await chat_completion(
            system=_SYSTEM_PROMPT,
            user=user_message,
            max_tokens=_MAX_TOKENS,
        )
        if response is None:
            logger.error("[OpinionAnalyzer] LLM call failed")
            return None
        return _parse_result(response.content)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是一个专业的社交媒体舆情分析师。
你的任务是分析评论列表，判断哪些评论与给定的情绪观点相关，并总结整体舆情模式。

判断相关性的标准：
  - 评论内容与触发事件或情绪直接相关（讨论同一件事、同一种情绪）
  - 不要求情绪方向一致（反对意见也是相关评论）
  - 纯广告、无意义符号、与主题完全无关的内容为不相关

输出必须是合法的 JSON，不加任何其他文字。
"""


def _build_opinion_prompt(sentiment: Sentiment, comments: list[RawComment]) -> str:
    # 按热度（likes）排序，优先分析高热度评论
    sorted_comments = sorted(comments, key=lambda c: c.likes, reverse=True)

    comments_text = "\n".join(
        f"[{i+1}] @{c.author_name}（{c.likes}赞）：{c.content}"
        for i, c in enumerate(sorted_comments[:50])  # 最多送入50条
    )

    return f"""\
## 待分析的情绪观点

情绪概括：{sentiment.summary}
触发事件：{sentiment.trigger_event}
情绪类型：{sentiment.emotion_label or '未知'}
作者与事件关系：{sentiment.author_relation}

## 评论列表（共{len(sorted_comments)}条，按热度排序）

{comments_text}

## 分析任务

请完成以下分析，严格输出 JSON：

```json
{{
  "total_analyzed": {len(sorted_comments[:50])},
  "relevant_count": <与上述情绪相关的评论数>,
  "relevance_ratio": <relevant_count / total_analyzed，保留2位小数>,
  "resonance_level": "<high|medium|low|negligible>",
  "dominant_emotion": "<相关评论中主导情绪>",
  "emotion_distribution": {{
    "<情绪类型>": <占比0-1>
  }},
  "emotion_diversity": <情绪多样性0-1>,
  "summary": "<对评论区舆情的1-2句整体描述>",
  "representative_comments": [
    {{
      "author": "<评论者>",
      "content": "<评论内容>",
      "emotion": "<该条评论情绪>",
      "likes": <点赞数>
    }}
  ],
  "analysis_notes": "<分析备注或null>"
}}
```

resonance_level 判断标准：
  high       = relevant_ratio > 0.6 且情绪方向基本一致
  medium     = relevant_ratio 0.3-0.6，或情绪方向有分歧
  low        = relevant_ratio 0.1-0.3
  negligible = relevant_ratio < 0.1
"""


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _parse_result(raw: str) -> ExtractedPublicOpinion | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        data = json.loads(json_str)
        return ExtractedPublicOpinion.model_validate(data)
    except Exception as exc:
        logger.warning(f"[OpinionAnalyzer] Parse error: {exc}\nRaw: {raw[:300]}")
        return None


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _comments_to_json(comments: list[RawComment]) -> str:
    return json.dumps(
        [
            {
                "author": c.author_name,
                "content": c.content,
                "likes": c.likes,
                "posted_at": c.posted_at.isoformat(),
            }
            for c in comments
        ],
        ensure_ascii=False,
    )
