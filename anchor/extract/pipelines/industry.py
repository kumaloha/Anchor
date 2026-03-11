"""
pipelines/industry.py — 产业链研究模式流水线
=============================================
标准 v6 提取的超集：先复用 extract_v6() 提取观点实体，
再追加 3 次 LLM 调用提取产业结构实体，最后建立跨层关系。

适用内容类型：content_type == "产业链研究"
"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    CanonicalPlayer,
    EntityRelationship,
    Fact,
    Conclusion,
    Issue,
    LayerSchema,
    Metric,
    PlayerAlias,
    RawPost,
    SupplyNode,
    TechRoute,
    Theory,
)
from anchor.extract.schemas import ExtractionResult
from anchor.extract.schemas.industry import (
    IndustryContextResult,
    IndustryEntitiesResult,
    IndustryRelationshipResult,
    IndustryEdge,
)
from anchor.extract.pipelines._base import call_llm, parse_json

import re as _re


def _recover_truncated_edges(raw: str) -> IndustryRelationshipResult | None:
    """从被截断的 JSON 输出中抢救已完成的 edge 对象。"""
    # 用正则匹配所有完整的 edge 对象
    pattern = _re.compile(
        r'\{\s*"source_type"\s*:\s*"([^"]+)"\s*,\s*'
        r'"source_id"\s*:\s*"([^"]+)"\s*,\s*'
        r'"target_type"\s*:\s*"([^"]+)"\s*,\s*'
        r'"target_id"\s*:\s*"([^"]+)"\s*,\s*'
        r'"edge_type"\s*:\s*"([^"]+)"\s*\}',
    )
    matches = pattern.findall(raw)
    if not matches:
        return None
    edges = [
        IndustryEdge(
            source_type=m[0], source_id=m[1],
            target_type=m[2], target_id=m[3],
            edge_type=m[4],
        )
        for m in matches
    ]
    logger.info(f"[industry] Recovered {len(edges)} edges from truncated output")
    return IndustryRelationshipResult(edges=edges)


_STEP1_TOKENS = 6000
_STEP2_TOKENS = 6000
_STEP3_TOKENS = 8000


# ── 主入口 ──────────────────────────────────────────────────────────────────


async def extract_industry(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
    author_intent: str | None,
    force: bool,
) -> ExtractionResult | None:
    """产业链研究模式：v6 标准提取 + 3 次产业 LLM 调用 + 归一化写入。"""

    # Phase A: 复用 v6 标准提取
    from anchor.extract.pipelines.financial import extract_v6
    v6_result = await extract_v6(
        raw_post, session, content, platform, author, today, author_intent, force,
    )
    if v6_result is None or not v6_result.is_relevant_content:
        return v6_result

    # Phase B: 产业实体提取（3 LLM calls）
    ctx = await _industry_step1_context(content, platform, author, today)
    if ctx is None:
        logger.warning(f"[industry] Step1 failed for RawPost id={raw_post.id}, graceful degradation")
        return v6_result

    logger.info(
        f"[industry] Step1 done: chain={ctx.industry_chain}, "
        f"{len(ctx.players)} players, {len(ctx.supply_nodes)} nodes"
    )

    entities = await _industry_step2_entities(content, ctx)
    if entities is None:
        logger.warning("[industry] Step2 failed, using empty entities")
        entities = IndustryEntitiesResult(issues=[], tech_routes=[], metrics=[])

    logger.info(
        f"[industry] Step2 done: {len(entities.issues)} issues, "
        f"{len(entities.tech_routes)} tech_routes, {len(entities.metrics)} metrics"
    )

    # 读取已写入 DB 的 opinion entity IDs 供 Call 3 使用
    opinion_summary = await _get_opinion_entity_summary(raw_post.id, session)

    cross_edges = await _industry_step3_relationships(content, ctx, entities, opinion_summary)
    if cross_edges is None:
        logger.warning("[industry] Step3 failed, skipping cross-layer edges")
        cross_edges = IndustryRelationshipResult(edges=[])

    logger.info(f"[industry] Step3 done: {len(cross_edges.edges)} edges")

    # Phase C: 归一化 + DB write
    await _normalize_and_write(ctx, entities, cross_edges, raw_post, session)

    return v6_result


# ── LLM call wrappers ──────────────────────────────────────────────────────


async def _industry_step1_context(
    content: str, platform: str, author: str, today: str,
) -> IndustryContextResult | None:
    from anchor.extract.prompts.industry import step1_context as p
    user_msg = p.build_user_message(content, platform, author, today)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP1_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, IndustryContextResult, "industry-Step1")


async def _industry_step2_entities(
    content: str, ctx: IndustryContextResult,
) -> IndustryEntitiesResult | None:
    from anchor.extract.prompts.industry import step2_entities as p
    user_msg = p.build_user_message(content, ctx)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP2_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, IndustryEntitiesResult, "industry-Step2")


async def _industry_step3_relationships(
    content: str,
    ctx: IndustryContextResult,
    entities: IndustryEntitiesResult,
    opinion_summary: str,
) -> IndustryRelationshipResult | None:
    from anchor.extract.prompts.industry import step3_relationships as p
    user_msg = p.build_user_message(content, ctx, entities, opinion_summary)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP3_TOKENS)
    if raw is None:
        return None
    result = parse_json(raw, IndustryRelationshipResult, "industry-Step3")
    if result is None:
        # 截断恢复：从不完整 JSON 中抢救已完成的 edge 对象
        result = _recover_truncated_edges(raw)
    return result


# ── 观点实体概览（供 Step 3 prompt 使用）─────────────────────────────────────


async def _get_opinion_entity_summary(
    raw_post_id: int, session: AsyncSession,
) -> str:
    """读取已写入 DB 的 v6 观点实体，生成简要概览文本供 Step 3 prompt 注入。"""
    lines: list[str] = []

    facts = (await session.exec(
        select(Fact).where(Fact.raw_post_id == raw_post_id)
    )).all()
    for f in facts:
        lines.append(f"  [fact_{f.id}] (fact) {f.summary}：{f.claim[:60]}")

    conclusions = (await session.exec(
        select(Conclusion).where(Conclusion.raw_post_id == raw_post_id)
    )).all()
    for c in conclusions:
        tag = " [核心]" if c.is_core_conclusion else ""
        lines.append(f"  [conclusion_{c.id}] (conclusion{tag}) {c.summary}：{c.claim[:60]}")

    theories = (await session.exec(
        select(Theory).where(Theory.raw_post_id == raw_post_id)
    )).all()
    for t in theories:
        lines.append(f"  [theory_{t.id}] (theory) {t.summary}：{t.claim[:60]}")

    return "\n".join(lines) if lines else ""


# ── 归一化 + DB 写入 ────────────────────────────────────────────────────────


async def _normalize_and_write(
    ctx: IndustryContextResult,
    entities: IndustryEntitiesResult,
    cross_edges: IndustryRelationshipResult,
    raw_post: RawPost,
    session: AsyncSession,
) -> None:
    """归一化产业实体并写入 DB。"""

    industry_chain = ctx.industry_chain

    # ── Player 归一化 ────────────────────────────────────────────────────────
    player_map: dict[str, int] = {}  # temp_id → canonical_player.id

    for ep in ctx.players:
        # 查 alias 精确匹配
        all_names = [ep.canonical_name] + ep.aliases
        found_id: int | None = None
        for name in all_names:
            alias_row = (await session.exec(
                select(PlayerAlias).where(PlayerAlias.alias == name)
            )).first()
            if alias_row:
                found_id = alias_row.canonical_player_id
                break

        if found_id:
            player_map[ep.temp_id] = found_id
        else:
            # 创建新 CanonicalPlayer
            cp = CanonicalPlayer(
                canonical_name=ep.canonical_name,
                entity_type=ep.entity_type,
                headquarters=ep.headquarters,
            )
            session.add(cp)
            await session.flush()
            player_map[ep.temp_id] = cp.id

            # 写入所有别名
            for alias in all_names:
                lang = "en" if all(ord(c) < 128 for c in alias) else "zh"
                pa = PlayerAlias(
                    canonical_player_id=cp.id,
                    alias=alias,
                    language=lang,
                )
                session.add(pa)

    await session.flush()

    # ── SupplyNode 去重 ──────────────────────────────────────────────────────
    node_map: dict[str, int] = {}  # temp_id → supply_node.id

    for en in ctx.supply_nodes:
        existing = (await session.exec(
            select(SupplyNode).where(
                SupplyNode.industry_chain == industry_chain,
                SupplyNode.tier_id == en.tier_id,
                SupplyNode.node_name == en.node_name,
            )
        )).first()

        if existing:
            node_map[en.temp_id] = existing.id
        else:
            sn = SupplyNode(
                industry_chain=industry_chain,
                tier_id=en.tier_id,
                layer_name=en.layer_name,
                node_name=en.node_name,
                description=en.description,
            )
            session.add(sn)
            await session.flush()
            node_map[en.temp_id] = sn.id

    # ── Issue ────────────────────────────────────────────────────────────────
    issue_map: dict[str, int] = {}  # temp_id → issue.id

    for ei in entities.issues:
        supply_node_id = node_map.get(ei.supply_node_ref) if ei.supply_node_ref else None
        issue = Issue(
            raw_post_id=raw_post.id,
            supply_node_id=supply_node_id,
            issue_text=ei.issue_text,
            severity=ei.severity,
            status=ei.status,
            resolution_progress=ei.resolution_progress,
            summary=ei.summary,
        )
        session.add(issue)
        await session.flush()
        issue_map[ei.temp_id] = issue.id

    # ── TechRoute ────────────────────────────────────────────────────────────
    techroute_map: dict[str, int] = {}  # temp_id → tech_route.id

    for et in entities.tech_routes:
        supply_node_id = node_map.get(et.supply_node_ref) if et.supply_node_ref else None
        tr = TechRoute(
            raw_post_id=raw_post.id,
            supply_node_id=supply_node_id,
            route_name=et.route_name,
            maturity=et.maturity,
            competing_routes=json.dumps(et.competing_routes, ensure_ascii=False) if et.competing_routes else None,
            summary=et.summary,
        )
        session.add(tr)
        await session.flush()
        techroute_map[et.temp_id] = tr.id

    # ── Metric + schema 匹配 ────────────────────────────────────────────────
    metric_map: dict[str, int] = {}  # temp_id → metric.id

    for em in entities.metrics:
        supply_node_id = node_map.get(em.supply_node_ref) if em.supply_node_ref else None
        canonical_player_id = player_map.get(em.player_ref) if em.player_ref else None

        # 查 LayerSchema 匹配
        is_schema = False
        if supply_node_id:
            # 先找 supply_node 的 tier_id
            sn = (await session.exec(
                select(SupplyNode).where(SupplyNode.id == supply_node_id)
            )).first()
            if sn:
                schema_match = (await session.exec(
                    select(LayerSchema).where(
                        LayerSchema.industry_chain == industry_chain,
                        LayerSchema.tier_id == sn.tier_id,
                        LayerSchema.metric_name == em.metric_name,
                    )
                )).first()
                if schema_match:
                    is_schema = True

        m = Metric(
            raw_post_id=raw_post.id,
            supply_node_id=supply_node_id,
            canonical_player_id=canonical_player_id,
            metric_name=em.metric_name,
            metric_value=em.metric_value,
            unit=em.unit,
            time_reference=em.time_reference,
            evidence_score=em.evidence_score,
            is_schema_metric=is_schema,
        )
        session.add(m)
        await session.flush()
        metric_map[em.temp_id] = m.id

    # ── 跨层关系边 ──────────────────────────────────────────────────────────

    # 建立 type → temp_id → db_id 的映射
    id_resolver: dict[str, dict[str, int]] = {
        "player": player_map,
        "supply_node": node_map,
        "issue": issue_map,
        "tech_route": techroute_map,
        "metric": metric_map,
    }

    for edge in cross_edges.edges:
        src_db_id = _resolve_id(edge.source_type, edge.source_id, id_resolver, raw_post.id, session)
        tgt_db_id = _resolve_id(edge.target_type, edge.target_id, id_resolver, raw_post.id, session)

        if src_db_id is None or tgt_db_id is None:
            logger.debug(
                f"[industry] Edge skipped: {edge.source_type}:{edge.source_id} → "
                f"{edge.target_type}:{edge.target_id} (unresolved)"
            )
            continue

        if src_db_id == tgt_db_id and edge.source_type == edge.target_type:
            continue  # self-loop

        rel = EntityRelationship(
            raw_post_id=raw_post.id,
            source_type=edge.source_type,
            source_id=src_db_id,
            target_type=edge.target_type,
            target_id=tgt_db_id,
            edge_type=edge.edge_type,
        )
        session.add(rel)

    await session.flush()
    await session.commit()

    # 统计
    n_players = len(player_map)
    n_nodes = len(node_map)
    n_issues = len(issue_map)
    n_techroutes = len(techroute_map)
    n_metrics = len(metric_map)
    n_edges = len(cross_edges.edges)
    logger.info(
        f"[industry] Written: {n_players} players, {n_nodes} nodes, "
        f"{n_issues} issues, {n_techroutes} tech_routes, "
        f"{n_metrics} metrics, {n_edges} cross-edges"
    )


def _resolve_id(
    entity_type: str,
    temp_or_db_id: str,
    id_resolver: dict[str, dict[str, int]],
    raw_post_id: int,
    session: AsyncSession,
) -> int | None:
    """将 LLM 输出的 temp_id 或 "type_N" 格式 ID 解析为 DB ID。"""

    # 产业实体：直接查 id_resolver
    if entity_type in id_resolver:
        return id_resolver[entity_type].get(temp_or_db_id)

    # 观点实体：格式 "fact_123" → 取 123
    if "_" in temp_or_db_id:
        try:
            return int(temp_or_db_id.split("_", 1)[1])
        except (ValueError, IndexError):
            return None

    # 纯数字
    try:
        return int(temp_or_db_id)
    except ValueError:
        return None
