"""
Chain 1 — 逻辑提炼链路
======================
输入：URL（支持 Twitter / Weibo / 通用 URL）
输出：写入 DB 的六实体 + EntityRelationship 边

流程：
  URL → process_url() → RawPost
      → Extractor.extract() → 六实体 + Relationship 边
      → DAG 分析（已在 Extractor 内完成）
      → 返回汇总信息

用法：
  async with AsyncSessionLocal() as session:
      result = await run_chain1("https://x.com/...", session)
      print(result)
"""

from __future__ import annotations

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.extractor import Extractor
from anchor.extract.schemas import ExtractionResult
from anchor.collect.input_handler import parse_url, process_url
from anchor.models import (
    Assumption,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    Prediction,
    RawPost,
    Solution,
)


async def run_chain1(url: str, session: AsyncSession) -> dict:
    """执行链路1：URL → 六实体提取

    Args:
        url:     帖子 URL（Twitter/Weibo/通用）
        session: 异步数据库 Session

    Returns:
        dict with keys:
          raw_post_id, author_id, author_name,
          facts, assumptions, implicit_conditions,
          conclusions, predictions, solutions, relationships,
          extraction_result (ExtractionResult | None),
          skipped (bool)
    """
    # ── Step 1：采集 RawPost ──────────────────────────────────────────────
    logger.info(f"[Chain1] Collecting URL: {url}")
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
        logger.error(f"[Chain1] RawPost not found after process_url for URL={url}")
        raise RuntimeError(f"RawPost not found for URL: {url}")

    raw_post_id = rp.id
    author_name = rp.author_name
    logger.info(f"[Chain1] RawPost id={raw_post_id}, author={author_name}")

    # ── Step 2：六实体提取 ────────────────────────────────────────────────
    extractor = Extractor()
    extraction: ExtractionResult | None = await extractor.extract(rp, session)

    if extraction is None:
        logger.warning(f"[Chain1] Extraction returned None for RawPost id={raw_post_id}")
        return {
            "raw_post_id": raw_post_id,
            "author_id": collect_result.author.id,
            "author_name": author_name,
            "skipped": True,
            "extraction_result": None,
            "facts": [], "assumptions": [], "implicit_conditions": [],
            "conclusions": [], "predictions": [], "solutions": [], "relationships": [],
        }

    if not extraction.is_relevant_content:
        logger.info(f"[Chain1] Content not relevant: {extraction.skip_reason}")
        return {
            "raw_post_id": raw_post_id,
            "author_id": collect_result.author.id,
            "author_name": author_name,
            "skipped": True,
            "skip_reason": extraction.skip_reason,
            "extraction_result": extraction,
            "facts": [], "assumptions": [], "implicit_conditions": [],
            "conclusions": [], "predictions": [], "solutions": [], "relationships": [],
        }

    # ── Step 3：从 DB 读取写入的实体汇总 ────────────────────────────────
    facts = list(
        (await session.exec(select(Fact).where(Fact.raw_post_id == raw_post_id))).all()
    )
    assumptions = list(
        (await session.exec(select(Assumption).where(Assumption.raw_post_id == raw_post_id))).all()
    )
    implicit_conditions = list(
        (await session.exec(
            select(ImplicitCondition).where(ImplicitCondition.raw_post_id == raw_post_id)
        )).all()
    )
    conclusions = list(
        (await session.exec(
            select(Conclusion).where(Conclusion.raw_post_id == raw_post_id)
        )).all()
    )
    predictions = list(
        (await session.exec(
            select(Prediction).where(Prediction.raw_post_id == raw_post_id)
        )).all()
    )
    solutions = list(
        (await session.exec(
            select(Solution).where(Solution.raw_post_id == raw_post_id)
        )).all()
    )
    relationships = list(
        (await session.exec(
            select(EntityRelationship).where(EntityRelationship.raw_post_id == raw_post_id)
        )).all()
    )

    logger.info(
        f"[Chain1] Done: {len(facts)} facts, {len(assumptions)} assumptions, "
        f"{len(implicit_conditions)} implicit, {len(conclusions)} conclusions, "
        f"{len(predictions)} predictions, {len(solutions)} solutions, "
        f"{len(relationships)} edges"
    )

    return {
        "raw_post_id": raw_post_id,
        "author_id": collect_result.author.id,
        "author_name": author_name,
        "skipped": False,
        "extraction_result": extraction,
        "facts": facts,
        "assumptions": assumptions,
        "implicit_conditions": implicit_conditions,
        "conclusions": conclusions,
        "predictions": predictions,
        "solutions": solutions,
        "relationships": relationships,
    }
