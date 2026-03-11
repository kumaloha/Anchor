"""
pipelines/policy.py — 政策解读流水线
======================================
适用内容类型：政策解读
Policy v3：主旨扫描 → 并行搜索+上年文档 → 完整提取 → 摘要 → DB写入
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    Conclusion, EntityRelationship, Fact, Policy, PolicyItem, PolicyMeasure,
    PolicyTheme, Prediction, RawPost, Solution, _utcnow,
)
from anchor.extract.schemas import (
    ExtractionResult, ExtractedConclusion, ExtractedFact,
    PolicyComparisonResult, PolicyExtractionResult, PolicySchema,
    Step1PolicyResult,
)
from anchor.extract.pipelines._base import call_llm, parse_json, get_or_create_author

_STEP1_TOKENS = 4000
_MAX_TOKENS = 6000
_SUMMARY_TOKENS = 1000
LONG_DOC_THRESHOLD = 15000


async def extract_policy(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
    author_intent: str | None,
) -> ExtractionResult | None:
    """Policy 模式完整提取流程（v3）"""
    import asyncio
    from anchor.verify.web_searcher import web_search

    # Step A: 主旨扫描
    themes = await _step1_policy_themes(content)
    logger.info(f"[v5/policy] Step A themes: {themes}")

    current_year = raw_post.posted_at.year if raw_post.posted_at else int(today[:4])
    prior_year = current_year - 1

    async def search_theme(theme: str) -> tuple[str, str]:
        query = f"{theme} {current_year} 政策背景 宏观形势"
        try:
            results = await web_search(query, max_results=3)
            if results:
                snippet = " ".join(
                    getattr(r, "snippet", "") or getattr(r, "content", "") or ""
                    for r in results[:2]
                )[:400]
            else:
                snippet = ""
        except Exception as e:
            logger.warning(f"[v5/policy] Web search failed for '{theme}': {e}")
            snippet = ""
        return theme, snippet

    web_ctx: dict[str, str] = {}
    if themes:
        search_coros = [search_theme(t) for t in themes]
        prior_post_coro = _find_prior_policy_post(prior_year, session)
        *search_results, prior_post = await asyncio.gather(
            *search_coros, prior_post_coro, return_exceptions=True
        )
        for pair in search_results:
            if isinstance(pair, tuple):
                web_ctx[pair[0]] = pair[1]
        if isinstance(prior_post, Exception):
            logger.warning(f"[v5/policy] _find_prior_policy_post error: {prior_post}")
            prior_post = None
    else:
        prior_post = await _find_prior_policy_post(prior_year, session)

    prior_content: str | None = None
    if prior_post and isinstance(prior_post, RawPost):
        prior_content = prior_post.enriched_content or prior_post.content
        logger.info(f"[v5/policy] Found prior year post id={prior_post.id}")
    else:
        logger.info(f"[v5/policy] No prior year post in DB, auto-fetching {prior_year} document")
        prior_content = await _fetch_prior_year_content(raw_post, prior_year)

    # Step B: 完整提取
    if len(content) > LONG_DOC_THRESHOLD:
        logger.info(
            f"[v5/policy] Long doc detected ({len(content)} chars > {LONG_DOC_THRESHOLD}), "
            f"switching to per-theme extraction"
        )
        result = await _extract_policy_long(content, prior_content, themes, web_ctx)
    else:
        result = await _step1_policy_full(content, prior_content, web_ctx, themes=themes)

    if result is None:
        logger.warning(f"[v5/policy] Full extract failed for RawPost id={raw_post.id}")
        return None

    if not result.is_relevant_content:
        logger.info(f"[v5/policy] RawPost {raw_post.id} not relevant: {result.skip_reason}")
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.commit()
        return ExtractionResult(is_relevant_content=False, skip_reason=result.skip_reason)

    n_policies = len(result.policies)
    n_measures = sum(len(p.measures) for p in result.policies)
    logger.info(
        f"[v5/policy] Step B done: {n_policies} policies, {n_measures} measures, "
        f"{len(result.facts)} facts, {len(result.conclusions)} conclusions"
    )

    # Step C: 叙事摘要
    core_conclusions_text = [c.text for c in result.conclusions]
    key_facts_text = [f.text for f in result.facts]
    article_summary: str | None = None
    step5 = await _step5_summary(core_conclusions_text, [], key_facts_text)
    if step5:
        article_summary = step5
        logger.info(f"[v5/policy] Step5 summary: {article_summary!r}")

    # DB 写入
    await _write_policy_v3_entities(result, raw_post, session, article_summary)

    facts_extracted = [
        ExtractedFact(summary=f.summary, claim=f.text, verifiable_statement=f.text)
        for f in result.facts
    ]
    conclusions_extracted = [
        ExtractedConclusion(summary=c.summary, claim=c.text, verifiable_statement=c.text)
        for c in result.conclusions
    ]
    return ExtractionResult(
        is_relevant_content=True,
        article_summary=article_summary,
        facts=facts_extracted,
        conclusions=conclusions_extracted,
    )


async def compare_policies(
    current_post_id: int,
    prior_post_id: int,
    session: AsyncSession,
) -> PolicyComparisonResult | None:
    """对比两篇政策文档，标注 change_type 并写入当年 PolicyItem，删除摘要写入 Fact。"""
    current_post_r = await session.exec(select(RawPost).where(RawPost.id == current_post_id))
    current_post = current_post_r.first()
    prior_post_r = await session.exec(select(RawPost).where(RawPost.id == prior_post_id))
    prior_post = prior_post_r.first()

    if current_post is None or prior_post is None:
        logger.error(
            f"[compare_policies] Post not found: current={current_post_id}, prior={prior_post_id}"
        )
        return None

    current_year = str(current_post.posted_at.year) if current_post.posted_at else "当年"
    prior_year = str(prior_post.posted_at.year) if prior_post.posted_at else "上年"

    current_themes_r = await session.exec(
        select(PolicyTheme).where(PolicyTheme.raw_post_id == current_post_id)
    )
    current_themes = current_themes_r.all()
    theme_name_map: dict[int, str] = {t.id: t.theme_name for t in current_themes}

    current_items_r = await session.exec(
        select(PolicyItem).where(PolicyItem.raw_post_id == current_post_id)
    )
    current_items = current_items_r.all()

    prior_themes_r = await session.exec(
        select(PolicyTheme).where(PolicyTheme.raw_post_id == prior_post_id)
    )
    prior_themes = prior_themes_r.all()
    prior_theme_name_map: dict[int, str] = {t.id: t.theme_name for t in prior_themes}

    prior_items_r = await session.exec(
        select(PolicyItem).where(PolicyItem.raw_post_id == prior_post_id)
    )
    prior_items = prior_items_r.all()

    if not current_items:
        logger.warning(f"[compare_policies] No policy items for current post {current_post_id}")
        return None

    already_annotated = any(item.change_type is not None for item in current_items)
    if already_annotated:
        logger.info(f"[compare_policies] post {current_post_id} already annotated, skipping")
        return None

    current_policies_list = [
        {
            "id": item.id,
            "theme": theme_name_map.get(item.policy_theme_id, ""),
            "summary": item.summary,
            "policy_text": item.policy_text,
            "metric_value": item.metric_value,
        }
        for item in current_items
    ]
    prior_policies_list = [
        {
            "theme": prior_theme_name_map.get(item.policy_theme_id, ""),
            "summary": item.summary,
            "policy_text": item.policy_text,
            "metric_value": item.metric_value,
        }
        for item in prior_items
    ]

    logger.info(
        f"[compare_policies] Comparing {len(current_policies_list)} current vs "
        f"{len(prior_policies_list)} prior policies"
    )

    comparison = await _compare_policy_llm(
        current_year, current_policies_list, prior_year, prior_policies_list
    )
    if comparison is None:
        logger.warning("[compare_policies] LLM comparison failed")
        return None

    item_map: dict[int, PolicyItem] = {item.id: item for item in current_items}
    for ann in comparison.annotations:
        item = item_map.get(ann.policy_id)
        if item is None:
            logger.warning(f"[compare_policies] policy_id {ann.policy_id} not found, skipping")
            continue
        item.change_type = ann.change_type
        item.change_note = ann.change_note
        session.add(item)

    for ds in comparison.deleted_summaries:
        summary_text = ds[:15] if len(ds) > 15 else ds
        db_fact = Fact(
            raw_post_id=current_post_id,
            summary=f"[删除] {summary_text}",
            claim=f"[删除] {ds}",
            verifiable_statement=f"上年政策「{ds}」在当年文件中被删除",
        )
        session.add(db_fact)

    await session.commit()

    logger.info(
        f"[compare_policies] Done: {len(comparison.annotations)} annotated, "
        f"{len(comparison.deleted_summaries)} deleted written as facts"
    )
    return comparison


async def fetch_prior_year_and_compare(
    current_post_id: int,
    session: AsyncSession,
    search_query: str | None = None,
) -> PolicyComparisonResult | None:
    """自动搜索上年同类政策文档，提取后与当年比对。"""
    import hashlib
    from datetime import datetime as _dt
    from anchor.extract.pipelines.policy import extract_policy

    current_post_r = await session.exec(select(RawPost).where(RawPost.id == current_post_id))
    current_post = current_post_r.first()
    if current_post is None:
        logger.error(f"[fetch_prior] post {current_post_id} not found")
        return None

    current_year = current_post.posted_at.year if current_post.posted_at else _dt.utcnow().year
    prior_year = current_year - 1

    existing_prior = await _find_prior_policy_post(prior_year, session)
    if existing_prior:
        logger.info(f"[fetch_prior] Found existing prior year post id={existing_prior.id} ({prior_year})")
        return await compare_policies(current_post_id, existing_prior.id, session)

    from anchor.verify.web_searcher import web_search
    query = search_query or f"{prior_year}年政府工作报告 全文"
    logger.info(f"[fetch_prior] Searching: {query!r}")
    results = await web_search(
        query, max_results=5,
        include_domains=["gov.cn", "xinhuanet.com", "npc.gov.cn", "people.com.cn"],
    )
    if not results:
        logger.warning("[fetch_prior] No search results found")
        return None

    best_url = results[0].url
    logger.info(f"[fetch_prior] Top result: {best_url}")

    from anchor.collect.web import WebCollector
    collector = WebCollector()
    post_data = await collector.collect_by_url(best_url)
    if post_data is None or not post_data.content or len(post_data.content) < 500:
        logger.warning(f"[fetch_prior] Jina fetch failed or too short for {best_url}")
        for r in results[1:]:
            post_data = await collector.collect_by_url(r.url)
            if post_data and len(post_data.content) >= 500:
                best_url = r.url
                break
        if post_data is None or len(post_data.content) < 500:
            logger.error("[fetch_prior] All URLs failed, aborting")
            return None

    external_id = hashlib.md5(best_url.encode()).hexdigest()[:16]
    existing_rp_r = await session.exec(
        select(RawPost).where(
            RawPost.source == "web", RawPost.external_id == external_id,
        )
    )
    prior_post = existing_rp_r.first()
    if prior_post is None:
        prior_post = RawPost(
            source="web", external_id=external_id,
            content=post_data.content,
            author_name=post_data.author_name or f"国务院/{prior_year}",
            url=best_url,
            posted_at=_dt(prior_year, 3, 5),
            is_processed=False,
        )
        session.add(prior_post)
        await session.flush()
        logger.info(
            f"[fetch_prior] Created RawPost id={prior_post.id} for "
            f"{prior_year} ({len(post_data.content)} chars)"
        )
    else:
        logger.info(f"[fetch_prior] Reusing existing RawPost id={prior_post.id}")

    if not prior_post.is_processed:
        today = _dt.utcnow().date().isoformat()
        content = prior_post.enriched_content or prior_post.content
        result = await extract_policy(
            prior_post, session, content,
            prior_post.source, prior_post.author_name, today, None,
        )
        if result is None or not result.is_relevant_content:
            logger.warning("[fetch_prior] Extraction failed or not relevant for prior year post")
            return None
        logger.info(
            f"[fetch_prior] {prior_year} report extracted: "
            f"{len(result.facts)} facts, {len(result.conclusions)} conclusions"
        )
    else:
        logger.info("[fetch_prior] Prior year post already extracted")

    return await compare_policies(current_post_id, prior_post.id, session)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _step1_policy_themes(content: str) -> list[str]:
    from anchor.extract.prompts.v5_step1_policy import (
        SYSTEM_THEME_SCAN, build_theme_scan_message,
    )
    user = build_theme_scan_message(content)
    raw = await call_llm(SYSTEM_THEME_SCAN, user, max_tokens=400)
    if not raw:
        return []
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
        data = json.loads(raw.strip())
        return data.get("themes", [])
    except Exception as e:
        logger.warning(f"[v5/policy] Theme scan parse failed: {e}")
        return []


async def _extract_paragraphs_for_theme(content: str, theme: str) -> str:
    from anchor.extract.prompts.v5_step1_policy import (
        SYSTEM_PARA_EXTRACT, build_para_extract_message,
    )
    result = await call_llm(
        SYSTEM_PARA_EXTRACT, build_para_extract_message(content, theme), max_tokens=2000
    )
    return result.strip() if result else "（无相关内容）"


async def _step1_policy_single_theme(
    theme: str,
    curr_paragraphs: str,
    prior_paragraphs: str | None,
    web_snippet: str | None,
) -> PolicySchema | None:
    from anchor.extract.prompts.v5_step1_policy import (
        SYSTEM_SINGLE_THEME, build_single_theme_message,
    )
    raw = await call_llm(
        SYSTEM_SINGLE_THEME,
        build_single_theme_message(theme, curr_paragraphs, prior_paragraphs, web_snippet),
        max_tokens=2000,
    )
    if not raw:
        return None
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return PolicySchema(**json.loads(raw))
    except Exception as e:
        logger.warning(f"[v5/policy/long] Single-theme parse failed ({theme}): {e}")
        return None


async def _extract_policy_long(
    content: str,
    prior_content: str | None,
    themes: list[str],
    web_ctx: dict[str, str],
) -> PolicyExtractionResult:
    """长文档专用：逐主旨并行提取。"""
    import asyncio
    from anchor.extract.schemas import RawClaim
    from anchor.extract.prompts.v5_step1_policy import (
        SYSTEM_FACTS_CONCLUSIONS, build_facts_conclusions_message,
    )

    sem_para = asyncio.Semaphore(5)
    sem_b = asyncio.Semaphore(3)

    async def get_paragraphs(theme: str) -> tuple[str, str, str | None]:
        async with sem_para:
            curr_p = await _extract_paragraphs_for_theme(content, theme)
            prior_p = (
                await _extract_paragraphs_for_theme(prior_content, theme)
                if prior_content else None
            )
        return theme, curr_p, prior_p

    async def run_step_b(theme: str, curr_p: str, prior_p: str | None) -> PolicySchema | None:
        async with sem_b:
            return await _step1_policy_single_theme(
                theme, curr_p, prior_p, web_ctx.get(theme)
            )

    logger.info(f"[v5/policy/long] Step A2: paragraph extraction for {len(themes)} themes")
    para_results = await asyncio.gather(
        *[get_paragraphs(t) for t in themes], return_exceptions=True
    )

    logger.info("[v5/policy/long] Step B: per-theme extraction")
    valid_paras = [r for r in para_results if isinstance(r, tuple)]
    policy_results = await asyncio.gather(
        *[run_step_b(theme, curr_p, prior_p) for theme, curr_p, prior_p in valid_paras],
        return_exceptions=True,
    )
    policies = [p for p in policy_results if isinstance(p, PolicySchema)]
    logger.info(f"[v5/policy/long] Step B done: {len(policies)}/{len(themes)} policies")

    facts: list[RawClaim] = []
    conclusions: list[RawClaim] = []
    try:
        fc_raw = await call_llm(
            SYSTEM_FACTS_CONCLUSIONS,
            build_facts_conclusions_message(content, prior_content),
            max_tokens=1500,
        )
        if fc_raw:
            fc_raw = fc_raw.strip()
            if fc_raw.startswith("```"):
                fc_raw = re.sub(r"^```[a-z]*\n?", "", fc_raw)
                fc_raw = re.sub(r"\n?```$", "", fc_raw)
            fc_data = json.loads(fc_raw)
            facts = [RawClaim(**f) for f in fc_data.get("facts", [])]
            conclusions = [RawClaim(**c) for c in fc_data.get("conclusions", [])]
            logger.info(f"[v5/policy/long] facts={len(facts)}, conclusions={len(conclusions)}")
    except Exception as e:
        logger.warning(f"[v5/policy/long] Facts/conclusions failed: {e}")

    return PolicyExtractionResult(
        is_relevant_content=True, policies=policies, facts=facts, conclusions=conclusions,
    )


async def _step1_policy_full(
    current_content: str,
    prior_content: str | None,
    web_ctx: dict[str, str],
    themes: list[str] | None = None,
) -> PolicyExtractionResult | None:
    from anchor.extract.prompts.v5_step1_policy import (
        SYSTEM_FULL_EXTRACT, build_full_extract_message,
    )
    user = build_full_extract_message(current_content, prior_content, web_ctx, themes=themes)
    raw = await call_llm(SYSTEM_FULL_EXTRACT, user, max_tokens=8000)
    if not raw:
        return None
    try:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
        data = json.loads(raw.strip())
        return PolicyExtractionResult(**data)
    except Exception as e:
        logger.warning(f"[v5/policy] Full extract parse failed: {e}")
        return None


async def _step5_summary(
    core_conclusions: list[str], sub_conclusions: list[str], key_facts: list[str],
) -> str | None:
    from pydantic import BaseModel as _BM

    class _SummaryResult(_BM):
        summary: str

    from anchor.extract.prompts import v5_step5_summary as p
    user_msg = p.build_user_message(core_conclusions, sub_conclusions, key_facts)
    raw = await call_llm(p.SYSTEM, user_msg, _SUMMARY_TOKENS)
    if raw is None:
        return None
    parsed = parse_json(raw, _SummaryResult, "Step5")
    return parsed.summary if parsed else None


async def _compare_policy_llm(
    current_year: str,
    current_policies: list[dict],
    prior_year: str,
    prior_policies: list[dict],
) -> PolicyComparisonResult | None:
    from anchor.extract.prompts import v5_compare_policy as p
    user_msg = p.build_user_message(current_year, current_policies, prior_year, prior_policies)
    raw = await call_llm(p.SYSTEM, user_msg, _MAX_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, PolicyComparisonResult, "ComparePolicy")


async def _write_policy_v3_entities(
    result: PolicyExtractionResult,
    raw_post: RawPost,
    session: AsyncSession,
    article_summary: str | None = None,
) -> None:
    """将 PolicyExtractionResult 写入数据库（policies / policy_measures / facts / conclusions）"""
    conclusion_db_ids: list[int] = []
    fact_db_ids: list[int] = []

    for p_schema in result.policies:
        db_policy = Policy(
            raw_post_id=raw_post.id,
            theme=p_schema.theme, change_summary=p_schema.change_summary,
            target=p_schema.target, target_prev=p_schema.target_prev,
            intensity=p_schema.intensity, intensity_prev=p_schema.intensity_prev,
            intensity_note=p_schema.intensity_note,
            intensity_note_prev=p_schema.intensity_note_prev,
            background=p_schema.background, background_prev=p_schema.background_prev,
            organization=p_schema.organization, organization_prev=p_schema.organization_prev,
        )
        session.add(db_policy)
        await session.flush()

        for m_schema in p_schema.measures:
            db_measure = PolicyMeasure(
                policy_id=db_policy.id, raw_post_id=raw_post.id,
                summary=m_schema.summary, measure_text=m_schema.measure_text,
                trend=m_schema.trend, trend_note=m_schema.trend_note,
            )
            session.add(db_measure)

    for f in result.facts:
        db_fact = Fact(
            raw_post_id=raw_post.id, summary=f.summary,
            claim=f.text, verifiable_statement=f.text,
        )
        session.add(db_fact)
        await session.flush()
        fact_db_ids.append(db_fact.id)

    for c in result.conclusions:
        db_conc = Conclusion(
            raw_post_id=raw_post.id, summary=c.summary,
            claim=c.text, verifiable_statement=c.text, is_core_conclusion=True,
        )
        session.add(db_conc)
        await session.flush()
        conclusion_db_ids.append(db_conc.id)

    for fact_id in fact_db_ids:
        for conc_id in conclusion_db_ids:
            db_rel = EntityRelationship(
                raw_post_id=raw_post.id,
                source_type="fact", source_id=fact_id,
                target_type="conclusion", target_id=conc_id,
                edge_type="fact_supports_conclusion",
            )
            session.add(db_rel)

    raw_post.is_processed = True
    raw_post.processed_at = _utcnow()
    if article_summary:
        raw_post.content_summary = article_summary
    session.add(raw_post)
    await session.flush()
    await session.commit()

    logger.info(
        f"[v5/policy/v3] RawPost {raw_post.id} written: "
        f"{len(result.policies)} policies, {len(fact_db_ids)} facts, "
        f"{len(conclusion_db_ids)} conclusions"
    )


async def _find_prior_policy_post(prior_year: int, session: AsyncSession) -> RawPost | None:
    """在 DB 中查找已提取过 policy 的上年文档。"""
    from datetime import datetime as _dt
    year_start = _dt(prior_year, 1, 1)
    year_end = _dt(prior_year, 12, 31)
    r = await session.exec(
        select(RawPost)
        .join(Policy, Policy.raw_post_id == RawPost.id)
        .where(RawPost.posted_at >= year_start, RawPost.posted_at <= year_end)
        .limit(1)
    )
    post = r.first()
    if post:
        return post
    r = await session.exec(
        select(RawPost)
        .join(PolicyTheme, PolicyTheme.raw_post_id == RawPost.id)
        .where(RawPost.posted_at >= year_start, RawPost.posted_at <= year_end)
        .limit(1)
    )
    return r.first()


async def _fetch_prior_year_content(current_post: RawPost, prior_year: int) -> str | None:
    """联网搜索并抓取上年同类政策文档全文（仅用于对比，不写 DB）。"""
    from anchor.verify.web_searcher import web_search
    from anchor.collect.web import WebCollector

    topic = current_post.content_topic or ""
    if "政府工作报告" in (current_post.content or "") or "政府工作报告" in topic:
        query = f"{prior_year}年政府工作报告 全文"
    else:
        query = f"{prior_year}年 {topic or '政策文件'} 全文"

    logger.info(f"[v5/policy] Searching prior year doc: {query!r}")
    try:
        results = await web_search(
            query, max_results=5,
            include_domains=["gov.cn", "xinhuanet.com", "npc.gov.cn", "people.com.cn"],
        )
    except Exception as e:
        logger.warning(f"[v5/policy] Prior year search failed: {e}")
        return None

    if not results:
        logger.warning("[v5/policy] No search results for prior year doc")
        return None

    collector = WebCollector()
    for r in results[:3]:
        try:
            post_data = await collector.collect_by_url(r.url)
            if post_data and post_data.content and len(post_data.content) >= 500:
                logger.info(
                    f"[v5/policy] Prior year doc fetched from {r.url}: "
                    f"{len(post_data.content)} chars"
                )
                return post_data.content
        except Exception as e:
            logger.debug(f"[v5/policy] Fetch failed for {r.url}: {e}")
            continue

    logger.warning("[v5/policy] All prior year fetch attempts failed")
    return None
