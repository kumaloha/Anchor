"""
router.py — Extractor 门面（v8 — 统一 Node/Edge 架构）
======================================================
所有 content_mode 统一分发到 generic pipeline，只换领域提示词。

Usage:
    extractor = Extractor()
    result = await extractor.extract(raw_post, session, content_mode="expert")
"""

from __future__ import annotations

import datetime

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import RawPost


class Extractor:
    """观点提取器（v8 统一 Node/Edge 管线）"""

    def __init__(self) -> None:
        logger.info("Extractor initialized (v8 generic pipeline)")

    async def extract(
        self,
        raw_post: RawPost,
        session: AsyncSession,
        content_mode: str = "expert",
        author_intent: str | None = None,
        force: bool = False,
    ) -> dict | None:
        """对一条帖子执行节点+边提取，写入数据库，返回结果。

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

        content = raw_post.enriched_content or raw_post.content

        if raw_post.media_json:
            from anchor.collect.media_describer import describe_media
            media_desc = await describe_media(raw_post)
            if media_desc:
                content = content + "\n\n--- 图片内容 ---\n" + media_desc

        today = (raw_post.posted_at or datetime.datetime.utcnow()).date().isoformat()
        platform = raw_post.source
        author = raw_post.author_name

        logger.info(f"[v8] Extracting RawPost id={raw_post.id} domain={content_mode}")

        from anchor.extract.pipelines.generic import extract_generic
        return await extract_generic(
            raw_post, session, content, platform, author, today,
            domain=content_mode,
            author_intent=author_intent,
            force=force,
        )
