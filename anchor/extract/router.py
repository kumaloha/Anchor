"""
router.py — Extractor 门面（v9 — 域专用管线架构）
==================================================
company 域走专用提取管线（13 张表），其他域暂时禁用。

Usage:
    extractor = Extractor()
    result = await extractor.extract(raw_post, session, content_mode="company")
"""

from __future__ import annotations

import datetime

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.config import settings
from anchor.models import RawPost


class Extractor:
    """观点提取器（v9 域专用管线）"""

    def __init__(self) -> None:
        logger.info("Extractor initialized (v9 domain-specific pipelines)")

    async def extract(
        self,
        raw_post: RawPost,
        session: AsyncSession,
        content_mode: str = "expert",
        author_intent: str | None = None,
        force: bool = False,
    ) -> dict | None:
        """对一条帖子执行提取，写入数据库，返回结果。

        Args:
            raw_post:      待处理的原始帖子
            session:       异步数据库 Session
            content_mode:  领域：policy|industry|technology|futures|company|expert
            author_intent: 通用判断前置分类的作者意图
            force:         True 时跳过 is_processed 检查
        """
        if not force and raw_post.is_processed:
            logger.debug(f"RawPost {raw_post.id} already processed, skipping")
            return None

        if raw_post.is_duplicate:
            logger.info(
                f"RawPost {raw_post.id} is a cross-platform duplicate, skipping"
            )
            return None

        # ── 域开关检查 ─────────────────────────────────────────────────
        if not settings.is_domain_enabled(content_mode):
            logger.info(
                f"[Extractor] Domain '{content_mode}' is disabled, skipping extraction"
            )
            return {
                "is_relevant_content": False,
                "skip_reason": f"域 '{content_mode}' 已禁用，跳过提取",
                "domain_disabled": True,
            }

        content = raw_post.enriched_content or raw_post.content

        if raw_post.media_json:
            from anchor.collect.media_describer import describe_media
            media_desc = await describe_media(raw_post)
            if media_desc:
                content = content + "\n\n--- 图片内容 ---\n" + media_desc

        today = (raw_post.posted_at or datetime.datetime.utcnow()).date().isoformat()
        platform = raw_post.source
        author = raw_post.author_name

        logger.info(f"[Extractor] Extracting RawPost id={raw_post.id} domain={content_mode}")

        # ── 域路由 ─────────────────────────────────────────────────────
        if content_mode == "company":
            from anchor.extract.pipelines.company import extract_company
            return await extract_company(
                raw_post, session, content, platform, author, today,
            )

        # 其他域暂时禁用（不应走到这里，因为域开关已过滤）
        logger.warning(f"[Extractor] Domain '{content_mode}' has no pipeline, skipping")
        return {
            "is_relevant_content": False,
            "skip_reason": f"域 '{content_mode}' 尚无专用管线",
        }
