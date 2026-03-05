"""
Layer1 Step B — 跨平台内容去重
================================
采集到新内容后，判断每篇新帖是否是同一作者在其他平台已有内容的重复/转发。
若是，则标记 RawPost.is_duplicate=True + original_post_id，Layer2 提取时跳过。

候选帖筛选（减少 LLM 调用）：
  - 同一 author 的其他平台监控源（MonitoredSource）下的帖子
  - 最近 30 天内
  - 内容长度在新帖的 50%~200% 范围内（避免误判）
  - 最多取 10 个候选

LLM 判断输出格式：
  {
    "is_duplicate": true,
    "original_post_id": 15,
    "similarity": 0.95,
    "reason": "内容完全相同，仅格式略有差异"
  }
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import MonitoredSource, RawPost

_SYSTEM = """你是一个内容去重系统。
给定一篇新帖和一组候选帖，判断新帖是否是某个候选帖的跨平台重复或转发（内容实质相同，仅因平台差异导致格式略有不同）。
注意：内容主题相同但表达完全不同的帖子不算重复。
仅输出 JSON，不要任何其他内容。"""


class ContentDuplicateChecker:
    """Layer1 Step B：保存新 RawPost 后，检查是否跨平台重复。"""

    async def check(
        self, author_id: int, new_posts: list[RawPost], session: AsyncSession
    ) -> None:
        """对每篇新帖执行去重检查，标记重复项。"""
        for post in new_posts:
            try:
                await self._check_one(author_id, post, session)
            except Exception as exc:
                logger.warning(
                    f"[ContentDuplicateChecker] Error checking post id={post.id}: {exc}"
                )

    async def _check_one(
        self, author_id: int, post: RawPost, session: AsyncSession
    ) -> None:
        candidates = await self._find_candidates(author_id, post, session)
        if not candidates:
            return

        result = await self._call_llm(post, candidates)
        if result is None:
            return

        if result.get("is_duplicate"):
            orig_id = result.get("original_post_id")
            post.is_duplicate = True
            post.original_post_id = orig_id
            session.add(post)
            logger.info(
                f"[ContentDuplicateChecker] Post id={post.id} ({post.source}) "
                f"marked as duplicate of post_id={orig_id} "
                f"similarity={result.get('similarity', '?')} | {result.get('reason', '')}"
            )

    async def _find_candidates(
        self, author_id: int, post: RawPost, session: AsyncSession
    ) -> list[RawPost]:
        """查找候选：同一 author 的不同平台监控源下的近 30 天帖子。"""
        # 找该作者在其他平台的所有监控源
        sources_r = await session.exec(
            select(MonitoredSource).where(
                MonitoredSource.author_id == author_id,
                MonitoredSource.platform != post.source,
            )
        )
        source_ids = [s.id for s in sources_r.all()]
        if not source_ids:
            return []

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
        result = await session.exec(
            select(RawPost).where(
                RawPost.monitored_source_id.in_(source_ids),
                RawPost.collected_at >= cutoff,
                RawPost.id != (post.id or -1),
                RawPost.is_duplicate == False,
            ).limit(10)
        )
        candidates = list(result.all())

        # 按内容长度过滤（50%~200%），排除长度差异悬殊的帖子
        post_len = len(post.content or "")
        if post_len == 0:
            return []

        return [
            c for c in candidates
            if c.content and 0.5 <= len(c.content) / post_len <= 2.0
        ]

    async def _call_llm(self, post: RawPost, candidates: list[RawPost]) -> dict | None:
        cands_info = [
            {
                "id": c.id,
                "source": c.source,
                "content_preview": (c.content or "")[:500],
            }
            for c in candidates
        ]
        user = (
            f"新帖（source={post.source!r}, id={post.id}）内容：\n"
            f"{(post.content or '')[:800]}\n\n"
            f"候选帖列表：\n{json.dumps(cands_info, ensure_ascii=False, indent=2)}\n\n"
            f"判断新帖是否是某个候选帖的跨平台重复或转发。\n"
            f"输出格式：\n"
            f'{{"is_duplicate": true/false, "original_post_id": <int或null>, '
            f'"similarity": <0.0-1.0>, "reason": "<简短说明>"}}'
        )

        resp = await chat_completion(system=_SYSTEM, user=user, max_tokens=200)
        if resp is None:
            return None

        try:
            raw = resp.content.strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(raw[start:end])
        except Exception as exc:
            logger.warning(
                f"[ContentDuplicateChecker] Failed to parse LLM response: {exc}"
            )
            return None
