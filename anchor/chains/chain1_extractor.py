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
    Effect,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    Limitation,
    Prediction,
    Problem,
    RawPost,
    Solution,
)

_POLICY_TYPES = {"政策宣布", "政策解读"}
_INDUSTRY_TYPES = {"产业链研究", "财经分析"}


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

    # ── Step 2：内容预分类（Chain 2 Step 1+2 前置）────────────────────────
    from anchor.chains.chain2_author import classify_post
    logger.info(f"[Chain1] Pre-classifying RawPost id={raw_post_id}")
    pre = await classify_post(rp, session)
    content_type = pre.get("content_type")
    author_intent = pre.get("author_intent")
    logger.info(f"[Chain1] Pre-classification done: type={content_type!r} intent={author_intent!r}")

    # ── Step 3：内容路由 ───────────────────────────────────────────────────
    # 市场分析类 → standard（六实体流水线）
    # 政策宣布/解读 → policy（PolicyTheme + PolicyItem，change_type 由 compare_policies 单独填写）
    if content_type in _POLICY_TYPES:
        content_mode = "policy"
    elif content_type in _INDUSTRY_TYPES:
        content_mode = "industry"
    else:
        content_mode = "standard"
    if content_mode != "standard":
        logger.info(f"[Chain1] {content_mode.capitalize()} mode ({content_type})")

    # ── Step 4：六实体提取 ────────────────────────────────────────────────
    extractor = Extractor()
    extraction: ExtractionResult | None = await extractor.extract(
        rp, session,
        content_mode=content_mode,
        author_intent=author_intent,
    )

    # ── Step 4b：政策模式 — 自动搜索上年文档并比对 ────────────────────────
    if content_mode == "policy" and extraction and extraction.is_relevant_content:
        logger.info(f"[Chain1] Policy mode: auto-fetching prior year document for comparison")
        comparison = await extractor.fetch_prior_year_and_compare(rp.id, session)
        if comparison:
            logger.info(
                f"[Chain1] Policy comparison done: {len(comparison.annotations)} annotated, "
                f"{len(comparison.deleted_summaries)} deleted"
            )
        else:
            logger.warning("[Chain1] Policy comparison skipped or failed")

    if extraction is None:
        logger.warning(f"[Chain1] Extraction returned None for RawPost id={raw_post_id}")
        return {
            "raw_post_id": raw_post_id,
            "author_id": collect_result.author.id,
            "author_name": author_name,
            "skipped": True,
            "extraction_result": None,
            "facts": [], "assumptions": [], "implicit_conditions": [],
            "conclusions": [], "predictions": [], "solutions": [],
            "problems": [], "effects": [], "limitations": [],
            "relationships": [],
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
            "conclusions": [], "predictions": [], "solutions": [],
            "problems": [], "effects": [], "limitations": [],
            "relationships": [],
        }

    # ── Step 5：从 DB 读取写入的实体汇总 ────────────────────────────────
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
    problems = list(
        (await session.exec(
            select(Problem).where(Problem.raw_post_id == raw_post_id)
        )).all()
    )
    effects = list(
        (await session.exec(
            select(Effect).where(Effect.raw_post_id == raw_post_id)
        )).all()
    )
    limitations = list(
        (await session.exec(
            select(Limitation).where(Limitation.raw_post_id == raw_post_id)
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
        f"{len(problems)} problems, {len(effects)} effects, "
        f"{len(limitations)} limitations, {len(relationships)} edges"
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
        "problems": problems,
        "effects": effects,
        "limitations": limitations,
        "relationships": relationships,
    }
