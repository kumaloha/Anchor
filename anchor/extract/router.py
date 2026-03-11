"""
router.py — Extractor 门面
============================
对外接口不变，内部按 content_type 路由到对应 pipeline。

Usage:
    extractor = Extractor()
    result = await extractor.extract(raw_post, session, content_mode="standard")
"""

from __future__ import annotations

import datetime
from typing import Optional

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import RawPost
from anchor.extract.schemas import ExtractionResult, PolicyComparisonResult


class Extractor:
    """观点提取器（v5/v6 多阶段流水线）

    Usage:
        extractor = Extractor()
        result = await extractor.extract(raw_post, session)
    """

    def __init__(self) -> None:
        from anchor.extract.prompts import DEFAULT_PROMPT_VERSION
        self._version = DEFAULT_PROMPT_VERSION
        logger.info(f"Extractor initialized ({self._version} pipeline)")

    async def extract(
        self,
        raw_post: RawPost,
        session: AsyncSession,
        content_mode: str = "standard",
        author_intent: str | None = None,
        force: bool = False,
    ) -> ExtractionResult | None:
        """对一条帖子执行六实体提取，写入数据库，返回提取结果。

        Args:
            raw_post:      待处理的原始帖子
            session:       异步数据库 Session
            content_mode:  "standard"（默认六实体流水线）或 "policy"（政策模式）
            author_intent: Chain 2 前置分类预判的作者意图（注入 Step 1 提示词）
            force:         True 时跳过 is_processed / is_relevant_content 检查
        """
        if not force and raw_post.is_processed:
            logger.debug(f"RawPost {raw_post.id} already processed, skipping")
            return None

        if raw_post.is_duplicate:
            logger.info(
                f"RawPost {raw_post.id} is a cross-platform duplicate, skipping extraction"
            )
            return None

        content = raw_post.enriched_content or raw_post.content

        if raw_post.media_json:
            from anchor.collect.media_describer import describe_media
            media_desc = await describe_media(raw_post)
            if media_desc:
                content = content + "\n\n--- 图片内容 ---\n" + media_desc

        today = (raw_post.posted_at or datetime.datetime.utcnow()).date().isoformat()
        platform = raw_post.source
        author = raw_post.author_name

        logger.info(f"[{self._version}] Extracting RawPost id={raw_post.id}")

        # ── Policy 模式 ──────────────────────────────────────────────────────
        if content_mode == "policy":
            from anchor.extract.pipelines.policy import extract_policy
            return await extract_policy(
                raw_post, session, content, platform, author, today, author_intent,
            )

        # ── Industry 模式 ────────────────────────────────────────────────────
        if content_mode == "industry":
            from anchor.extract.pipelines.industry import extract_industry
            return await extract_industry(
                raw_post, session, content, platform, author, today, author_intent, force,
            )

        # ── v6 Top-Down Pipeline (default) ───────────────────────────────────
        if self._version == "v6":
            from anchor.extract.pipelines.financial import extract_v6
            return await extract_v6(
                raw_post, session, content, platform, author, today, author_intent, force,
            )

        # ── v5 Fallback ──────────────────────────────────────────────────────
        from anchor.extract.pipelines.financial_v5 import extract_v5
        return await extract_v5(
            raw_post, session, content, platform, author, today, author_intent, force,
        )

    async def compare_policies(
        self,
        current_post_id: int,
        prior_post_id: int,
        session: AsyncSession,
    ) -> PolicyComparisonResult | None:
        """对比两篇政策文档，标注 change_type。"""
        from anchor.extract.pipelines.policy import compare_policies
        return await compare_policies(current_post_id, prior_post_id, session)

    async def fetch_prior_year_and_compare(
        self,
        current_post_id: int,
        session: AsyncSession,
        search_query: str | None = None,
    ) -> PolicyComparisonResult | None:
        """自动搜索上年同类政策文档，提取后与当年比对。"""
        from anchor.extract.pipelines.policy import fetch_prior_year_and_compare
        return await fetch_prior_year_and_compare(
            current_post_id, session, search_query,
        )
