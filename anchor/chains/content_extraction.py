"""
内容提取 — Content Extraction（v8 Node/Edge 架构）
===================================================
输入：URL（支持 Twitter / Weibo / 通用 URL）
输出：写入 DB 的 Node + Edge

流程：
  URL → process_url() → RawPost
      → assess_post() → 通用判断（作者背景/立场/意图 + 文章分类）
      → 路由到对应领域 → generic pipeline 提取
      → 返回汇总信息

用法：
  async with AsyncSessionLocal() as session:
      result = await run_extraction("https://x.com/...", session)
      print(result)
"""

from __future__ import annotations

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.extractor import Extractor
from anchor.collect.input_handler import parse_url, process_url
from anchor.models import RawPost


async def run_extraction(url: str, session: AsyncSession) -> dict:
    """内容提取：URL → 通用判断 → 路由 → 节点+边提取

    Args:
        url:     帖子 URL（Twitter/Weibo/通用）
        session: 异步数据库 Session

    Returns:
        dict with keys:
          raw_post_id, author_id, author_name,
          nodes, edges, summary,
          extraction_result (dict | None),
          skipped (bool)
    """
    # ── Step 1：采集 RawPost ──────────────────────────────────────────────
    logger.info(f"[Extraction] Collecting URL: {url}")
    collect_result = await process_url(url, session)

    # 获取 RawPost（通过 parse_url 找到对应记录）
    parsed = parse_url(url)
    rp: RawPost | None = (
        await session.exec(
            select(RawPost).where(
                RawPost.source == parsed.platform,
                RawPost.external_id == parsed.platform_id,
            )
        )
    ).first()

    if not rp:
        # 降级：取最近一条
        rp = (
            await session.exec(
                select(RawPost)
                .where(RawPost.source == parsed.platform)
                .order_by(RawPost.id.desc())
            )
        ).first()

    if not rp:
        logger.error(f"[Extraction] RawPost not found after process_url for URL={url}")
        raise RuntimeError(f"RawPost not found for URL: {url}")

    raw_post_id = rp.id
    author_name = rp.author_name
    logger.info(f"[Extraction] RawPost id={raw_post_id}, author={author_name}")

    # ── Step 2：通用判断（作者背景 + 文章分类 + 摘要 + 利益冲突）────────
    from anchor.chains.general_assessment import assess_post, resolve_content_mode
    logger.info(f"[Extraction] Assessing RawPost id={raw_post_id}")
    pre = await assess_post(rp, session)
    author_intent = pre.get("author_intent")
    logger.info(
        f"[Extraction] Pre-classification done: domain={pre.get('content_domain')!r} "
        f"nature={pre.get('content_nature')!r} type={pre.get('content_type')!r}"
    )

    # ── Step 3：内容路由 ───────────────────────────────────────────────────
    content_mode = resolve_content_mode(
        pre.get("content_domain"), pre.get("content_nature"), pre.get("content_type"),
    )
    logger.info(f"[Extraction] Routed to domain={content_mode}")

    # ── Step 4：节点+边提取 ────────────────────────────────────────────────
    extractor = Extractor()
    extraction = await extractor.extract(
        rp, session,
        content_mode=content_mode,
        author_intent=author_intent,
    )

    if extraction is None:
        logger.warning(f"[Extraction] Extraction returned None for RawPost id={raw_post_id}")
        return {
            "raw_post_id": raw_post_id,
            "author_id": collect_result.author.id,
            "author_name": author_name,
            "skipped": True,
            "extraction_result": None,
            "nodes": [],
            "edges": [],
            "summary": None,
        }

    if not extraction.get("is_relevant_content", True):
        logger.info(f"[Extraction] Content not relevant: {extraction.get('skip_reason')}")
        return {
            "raw_post_id": raw_post_id,
            "author_id": collect_result.author.id,
            "author_name": author_name,
            "skipped": True,
            "skip_reason": extraction.get("skip_reason"),
            "extraction_result": extraction,
            "nodes": [],
            "edges": [],
            "summary": None,
        }

    # ── Step 5：返回提取结果 ────────────────────────────────────────────
    logger.info(
        f"[Extraction] Done: domain={content_mode}, "
        f"result keys={list(extraction.keys()) if extraction else 'None'}"
    )

    return {
        "raw_post_id": raw_post_id,
        "author_id": collect_result.author.id,
        "author_name": author_name,
        "skipped": False,
        "extraction_result": extraction,
        "summary": extraction.get("summary"),
    }
