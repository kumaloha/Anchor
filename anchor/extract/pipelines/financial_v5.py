"""
pipelines/financial_v5.py — 财经分析 v5 流水线（fallback）
============================================================
v5 多步流水线：原始声明提取 → 合并 → DAG → 分类 → 隐含条件 → 摘要 → DB写入
保留为 fallback，生产使用 financial.py (v6)。
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional, Set, Tuple

from loguru import logger
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    Assumption, Author, Conclusion, Effect, EntityRelationship,
    Fact, ImplicitCondition, Limitation, Prediction, Problem, RawPost,
    Solution, Theory, _utcnow,
)
from anchor.extract.schemas import (
    ClassifiedEntity, ExtractionResult, ImplicitConditionItem,
    MergeGroup, RawClaim, RawEdge, Step1Result, Step2Result, Step3Result, Step4Result,
)
from anchor.extract.pipelines._base import (
    call_llm, parse_json, normalize_confidence, find_cycle_nodes,
    get_or_create_author, apply_merges, get_entity_type, derive_edge_type,
    build_extraction_result_from_claims, to_extraction_result,
)

_STEP1_TOKENS = 4000
_STEP2_TOKENS = 2000
_STEP3_TOKENS = 4000
_STEP4_TOKENS = 3000
_STEP5_TOKENS = 1000


async def extract_v5(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
    author_intent: str | None,
    force: bool,
) -> ExtractionResult | None:
    """v5 multi-step pipeline."""
    from pydantic import BaseModel as _BM

    class _SummaryResult(_BM):
        summary: str

    # Step 1: 原始声明提取
    step1 = await _step1_claims(content, platform, author, today, author_intent)
    if step1 is None:
        logger.warning(f"[v5] Step1 failed for RawPost id={raw_post.id}")
        return None

    if not step1.is_relevant_content:
        logger.info(f"[v5] RawPost {raw_post.id} not relevant: {step1.skip_reason}")
        if not force:
            raw_post.is_processed = True
            raw_post.processed_at = _utcnow()
            session.add(raw_post)
            await session.commit()
            return ExtractionResult(is_relevant_content=False, skip_reason=step1.skip_reason)
        logger.info("[v5] force=True, continuing with empty claims")

    claims = step1.claims or []
    edges = step1.edges
    logger.info(f"[v5] Step1 done: {len(claims)} claims, {len(edges)} edges")

    # Step 2: 同义声明合并
    if len(claims) > 1:
        step2 = await _step2_merge(claims)
        if step2 is not None and step2.merges:
            claims, edges = apply_merges(claims, edges, step2.merges)
            logger.info(f"[v5] Step2 merge: {len(step2.merges)} groups → {len(claims)} claims remain")
        else:
            logger.info("[v5] Step2: no merges")
    else:
        logger.info("[v5] Step2: skipped (≤1 claim)")

    # Step 3: 构建 DAG + 识别核心/孤立节点
    claim_ids = {c.id for c in claims}
    in_degree: dict[int, int] = defaultdict(int)
    out_degree: dict[int, int] = defaultdict(int)
    for e in edges:
        if e.from_id in claim_ids and e.to_id in claim_ids:
            out_degree[e.from_id] += 1
            in_degree[e.to_id] += 1

    core_ids: Set[int] = {
        n for n in claim_ids if in_degree[n] > 0 and out_degree[n] == 0
    }
    isolated_ids: Set[int] = {
        n for n in claim_ids if in_degree[n] == 0 and out_degree[n] == 0
    }
    logger.info(f"[v5] DAG: core={len(core_ids)}, isolated={len(isolated_ids)}")

    # Step 4: 节点分类
    step3 = await _step3_classify(claims, edges, core_ids, isolated_ids)
    if step3 is None:
        logger.warning(f"[v5] Step3 (classify) failed for RawPost id={raw_post.id}")
        return None

    class_map: dict[int, ClassifiedEntity] = {
        ce.claim_id: ce for ce in step3.classifications
    }

    # Step 3b: 后处理 — 强制 Theory 上限
    class_map, edges = _step3_postprocess(class_map, edges, core_ids, claims)
    logger.info(f"[v5] Step3 done: {len(class_map)} classified")

    # Step 5: 批量生成隐含条件
    implicit_items: List[ImplicitConditionItem] = []
    claims_by_id = {c.id: c for c in claims}

    inferences: List[Tuple[str, str, int]] = []
    for e in edges:
        src = claims_by_id.get(e.from_id)
        tgt = claims_by_id.get(e.to_id)
        if src and tgt:
            inferences.append((src.text, tgt.text, tgt.id))

    from anchor.extract.prompts.v5_step4_implicit import _SOURCE_MARKER
    conclusion_claim_ids = {
        cid for cid, ce in class_map.items() if ce.entity_type == "conclusion"
    }
    source_conclusion_ids = conclusion_claim_ids - {e.to_id for e in edges}
    for cid in source_conclusion_ids:
        c = claims_by_id.get(cid)
        if c:
            inferences.append((_SOURCE_MARKER, c.text, cid))

    if inferences:
        step4 = await _step4_implicit(inferences)
        if step4 is not None:
            implicit_items = step4.implicit_conditions
            logger.info(f"[v5] Step4 done: {len(implicit_items)} implicit conditions")
    else:
        logger.info("[v5] Step4: skipped (no edges)")

    # Step 5b: 叙事摘要
    core_conclusions_text = [
        claims_by_id[cid].text for cid in core_ids if cid in claims_by_id
    ]
    sub_conclusions_text = [
        c.text for c in claims
        if c.id not in core_ids and c.id not in isolated_ids
        and class_map.get(c.id) and class_map[c.id].entity_type == "conclusion"
    ]
    key_facts_text = [
        c.text for c in claims
        if class_map.get(c.id) and class_map[c.id].entity_type == "fact"
    ]
    theory_texts = [
        c.text for c in claims
        if class_map.get(c.id) and class_map[c.id].entity_type == "theory"
    ]
    if theory_texts:
        sub_conclusions_text = sub_conclusions_text + [f"[理论] {t}" for t in theory_texts]

    article_summary: str | None = None
    step5 = await _step5_summary(core_conclusions_text, sub_conclusions_text, key_facts_text)
    if step5:
        article_summary = step5
        logger.info(f"[v5] Step5 summary: {article_summary!r}")
    else:
        logger.warning("[v5] Step5 summary failed, skipping")

    # Step 6: 构建提取结果
    result = build_extraction_result_from_claims(claims, edges, class_map, implicit_items)

    # Step 7: 写库
    _rp_id = raw_post.id
    for _tbl in (EntityRelationship, Limitation, Effect, Problem,
                 Solution, Theory, Prediction, Conclusion,
                 ImplicitCondition, Assumption, Fact):
        await session.exec(delete(_tbl).where(_tbl.raw_post_id == _rp_id))
    await session.flush()

    author_db = await get_or_create_author(session, raw_post)

    # 1. Fact
    fact_id_map: dict[int, int] = {}
    for claim_id, ef in result.get("facts", {}).items():
        db_fact = Fact(
            raw_post_id=raw_post.id, summary=ef.summary, claim=ef.claim,
            verifiable_statement=ef.verifiable_statement,
            temporal_type=ef.temporal_type, temporal_note=ef.temporal_note,
        )
        session.add(db_fact)
        await session.flush()
        fact_id_map[claim_id] = db_fact.id

    # 2. Assumption
    assumption_id_map: dict[int, int] = {}
    for claim_id, ea in result.get("assumptions", {}).items():
        db_assump = Assumption(
            raw_post_id=raw_post.id, summary=ea.summary,
            condition_text=ea.condition_text, verifiable_statement=ea.verifiable_statement,
            temporal_note=ea.temporal_note,
        )
        session.add(db_assump)
        await session.flush()
        assumption_id_map[claim_id] = db_assump.id

    # 3. ImplicitCondition
    implicit_db_list: list[tuple[int, int]] = []
    for item in implicit_items:
        db_impl = ImplicitCondition(
            raw_post_id=raw_post.id, summary=item.summary,
            condition_text=item.condition_text,
            is_obvious_consensus=item.is_obvious_consensus,
        )
        session.add(db_impl)
        await session.flush()
        implicit_db_list.append((item.target_claim_id, db_impl.id))

    # 4. Conclusion
    conclusion_id_map: dict[int, int] = {}
    conclusion_db_ids: list[int] = []
    for claim_id, ec in result.get("conclusions", {}).items():
        db_conc = Conclusion(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=ec.summary, claim=ec.claim,
            verifiable_statement=ec.verifiable_statement,
            author_confidence=ec.author_confidence,
        )
        session.add(db_conc)
        await session.flush()
        conclusion_id_map[claim_id] = db_conc.id
        conclusion_db_ids.append(db_conc.id)

    # 5. Prediction
    prediction_id_map: dict[int, int] = {}
    for claim_id, ep in result.get("predictions", {}).items():
        temporal_validity = "has_timeframe" if ep.temporal_note else "no_timeframe"
        db_pred = Prediction(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=ep.summary, claim=ep.claim,
            temporal_note=ep.temporal_note, temporal_validity=temporal_validity,
            author_confidence=ep.author_confidence,
        )
        session.add(db_pred)
        await session.flush()
        prediction_id_map[claim_id] = db_pred.id

    # 6. Solution
    solution_id_map: dict[int, int] = {}
    for claim_id, es in result.get("solutions", {}).items():
        db_sol = Solution(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=es.summary, claim=es.claim,
            action_type=es.action_type, action_target=es.action_target,
            action_rationale=es.action_rationale,
        )
        session.add(db_sol)
        await session.flush()
        solution_id_map[claim_id] = db_sol.id

    # 6b. Theory
    theory_id_map: dict[int, int] = {}
    for claim_id, eth in result.get("theories", {}).items():
        db_theory = Theory(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=eth.summary, claim=eth.claim,
        )
        session.add(db_theory)
        await session.flush()
        theory_id_map[claim_id] = db_theory.id

    # 6c. Problem
    problem_id_map: dict[int, int] = {}
    for claim_id, ep in result.get("problems", {}).items():
        db_problem = Problem(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=ep.summary, claim=ep.claim,
        )
        session.add(db_problem)
        await session.flush()
        problem_id_map[claim_id] = db_problem.id

    # 6d. Effect
    effect_id_map: dict[int, int] = {}
    for claim_id, ee in result.get("effects", {}).items():
        db_effect = Effect(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=ee.summary, claim=ee.claim,
        )
        session.add(db_effect)
        await session.flush()
        effect_id_map[claim_id] = db_effect.id

    # 6e. Limitation
    limitation_id_map: dict[int, int] = {}
    for claim_id, el in result.get("limitations", {}).items():
        db_limitation = Limitation(
            raw_post_id=raw_post.id, author_id=author_db.id,
            summary=el.summary, claim=el.claim,
        )
        session.add(db_limitation)
        await session.flush()
        limitation_id_map[claim_id] = db_limitation.id

    all_id_maps: dict[str, dict[int, int]] = {
        "fact": fact_id_map, "assumption": assumption_id_map,
        "conclusion": conclusion_id_map, "prediction": prediction_id_map,
        "solution": solution_id_map, "theory": theory_id_map,
        "problem": problem_id_map, "effect": effect_id_map,
        "limitation": limitation_id_map,
    }

    # 7. EntityRelationship 边
    premise_conclusion_ids: set[int] = set()
    conclusion_adj: dict[int, list[int]] = defaultdict(list)

    for e in edges:
        src_type = get_entity_type(e.from_id, class_map)
        tgt_type = get_entity_type(e.to_id, class_map)
        if src_type is None or tgt_type is None:
            continue

        src_db_id = all_id_maps.get(src_type, {}).get(e.from_id)
        tgt_db_id = all_id_maps.get(tgt_type, {}).get(e.to_id)
        if src_db_id is None or tgt_db_id is None:
            logger.warning(
                f"[v5] Edge skipped: {src_type}[{e.from_id}] → {tgt_type}[{e.to_id}] (db id missing)"
            )
            continue

        edge_type = derive_edge_type(src_type, tgt_type)
        db_rel = EntityRelationship(
            raw_post_id=raw_post.id,
            source_type=src_type, source_id=src_db_id,
            target_type=tgt_type, target_id=tgt_db_id,
            edge_type=edge_type,
        )
        session.add(db_rel)

        if edge_type == "conclusion_supports_conclusion":
            premise_conclusion_ids.add(src_db_id)
            conclusion_adj[src_db_id].append(tgt_db_id)

    # 7b. 隐含条件边
    for (target_claim_id, impl_db_id) in implicit_db_list:
        tgt_type = get_entity_type(target_claim_id, class_map)
        if tgt_type is None:
            continue
        tgt_db_id = all_id_maps.get(tgt_type, {}).get(target_claim_id)
        if tgt_db_id is None:
            continue
        db_rel = EntityRelationship(
            raw_post_id=raw_post.id,
            source_type="implicit_condition", source_id=impl_db_id,
            target_type=tgt_type, target_id=tgt_db_id,
            edge_type="implicit_conditions_conclusion",
        )
        session.add(db_rel)

    await session.flush()

    # 8. 识别核心结论
    for db_id in conclusion_db_ids:
        if db_id not in premise_conclusion_ids:
            conc_r = await session.exec(select(Conclusion).where(Conclusion.id == db_id))
            conc = conc_r.first()
            if conc:
                conc.is_core_conclusion = True
                session.add(conc)

    await session.flush()

    # 9. 检测结论环
    cycle_ids = find_cycle_nodes(conclusion_db_ids, conclusion_adj)
    if cycle_ids:
        logger.warning(f"[v5] Detected {len(cycle_ids)} conclusion(s) in cycle: {cycle_ids}")
        for db_id in cycle_ids:
            conc_r = await session.exec(select(Conclusion).where(Conclusion.id == db_id))
            conc = conc_r.first()
            if conc:
                conc.is_in_cycle = True
                session.add(conc)

    # 10. 标记 RawPost 已处理
    raw_post.is_processed = True
    raw_post.processed_at = _utcnow()
    if article_summary:
        raw_post.content_summary = article_summary
    session.add(raw_post)
    await session.flush()
    await session.commit()

    n_facts = len(result.get("facts", {}))
    n_assumptions = len(result.get("assumptions", {}))
    n_conclusions = len(result.get("conclusions", {}))
    logger.info(
        f"[v5] RawPost {raw_post.id} processed: "
        f"{n_facts} facts, {n_assumptions} assumptions, "
        f"{len(implicit_items)} implicit, {n_conclusions} conclusions, "
        f"{len(result.get('predictions', {}))} predictions, "
        f"{len(result.get('solutions', {}))} solutions, {len(edges)} edges"
    )

    extraction_result = to_extraction_result(result, implicit_items, edges, class_map)
    extraction_result.article_summary = article_summary
    return extraction_result


# LLM call wrappers

async def _step1_claims(
    content: str, platform: str, author: str, today: str,
    author_intent: str | None = None,
) -> Step1Result | None:
    from anchor.extract.prompts import v5_step1_claims as p
    user_msg = p.build_user_message(content, platform, author, today, author_intent)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP1_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, Step1Result, "Step1")


async def _step2_merge(claims: List[RawClaim]) -> Step2Result | None:
    from anchor.extract.prompts import v5_step2_merge as p
    user_msg = p.build_user_message(claims)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP2_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, Step2Result, "Step2")


async def _step3_classify(
    claims: List[RawClaim], edges: List[RawEdge],
    core_ids: Set[int], isolated_ids: Set[int],
) -> Step3Result | None:
    from anchor.extract.prompts import v5_step3_classify as p
    user_msg = p.build_user_message(claims, edges, core_ids, isolated_ids)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP3_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, Step3Result, "Step3")


async def _step4_implicit(inferences: List[Tuple[str, str, int]]) -> Step4Result | None:
    from anchor.extract.prompts import v5_step4_implicit as p
    user_msg = p.build_user_message(inferences)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP4_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, Step4Result, "Step4")


async def _step5_summary(
    core_conclusions: List[str], sub_conclusions: List[str], key_facts: List[str],
) -> str | None:
    from pydantic import BaseModel as _BM

    class _SummaryResult(_BM):
        summary: str

    from anchor.extract.prompts import v5_step5_summary as p
    user_msg = p.build_user_message(core_conclusions, sub_conclusions, key_facts)
    raw = await call_llm(p.SYSTEM, user_msg, _STEP5_TOKENS)
    if raw is None:
        return None
    parsed = parse_json(raw, _SummaryResult, "Step5")
    return parsed.summary if parsed else None


# Post-processing helpers

def _step3_postprocess(
    class_map: dict[int, ClassifiedEntity],
    edges: List[RawEdge],
    core_ids: Set[int],
    claims: List[RawClaim],
) -> tuple[dict[int, ClassifiedEntity], List[RawEdge]]:
    """Theory 上限 = 2，超出的降级为 Conclusion。"""
    MAX_THEORIES = 2
    theory_ids = [cid for cid, ce in class_map.items() if ce.entity_type == "theory"]
    if len(theory_ids) > MAX_THEORIES:
        _THEORY_KEYWORDS = {"理论", "模型", "原则", "周期", "框架", "Theory", "Model", "Cycle"}
        claims_by_id = {c.id: c for c in claims}

        def _theory_score(cid: int) -> int:
            text = claims_by_id.get(cid)
            if text is None:
                return 0
            return sum(1 for kw in _THEORY_KEYWORDS if kw in text.text)

        ranked = sorted(theory_ids, key=_theory_score, reverse=True)
        keep_set = set(ranked[:MAX_THEORIES])
        for tid in theory_ids:
            if tid not in keep_set:
                ce = class_map[tid]
                class_map[tid] = ClassifiedEntity(
                    claim_id=ce.claim_id, entity_type="conclusion",
                    author_confidence=ce.author_confidence or "likely",
                    verifiable_statement=ce.verifiable_statement,
                )
                logger.debug(f"[v5] Step3b: theory {tid} → conclusion (cap exceeded)")

    return class_map, edges
