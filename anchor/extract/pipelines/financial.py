"""
pipelines/financial.py — 财经分析 v6 流水线
=============================================
适用内容类型：财经分析 / 产业链研究 / 公司调研 / 技术论文
使用 v6 top-down 6-call pipeline。
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional

from loguru import logger
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    Assumption, Author, Conclusion, Effect, EntityRelationship,
    Fact, ImplicitCondition, Limitation, Prediction, Problem, RawPost,
    Solution, Theory, _utcnow,
)
from anchor.extract.schemas import (
    AbstractedResult, ExtractionResult, ExtractedAssumption, ExtractedConclusion,
    ExtractedEffect, ExtractedFact, ExtractedLimitation, ExtractedPrediction,
    ExtractedProblem, ExtractedSolution, ExtractedTheory,
    MergedResult, RelationshipResult, SupportingScanResult, TopDownAnchorsResult,
    TypedEdge, TypedEntity,
)
from anchor.extract.pipelines._base import (
    call_llm, parse_json, normalize_confidence, find_cycle_nodes,
    get_or_create_author, merge_llm_fields_by_position, apply_merge_decisions,
    derive_edge_type,
)

_V6_STEP1_TOKENS = 3000
_V6_STEP2_TOKENS = 6000
_V6_STEP3_TOKENS = 8000
_V6_STEP4_TOKENS = 5000
_V6_STEP5_TOKENS = 4000
_SUMMARY_TOKENS = 1000


async def extract_v6(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
    author_intent: str | None,
    force: bool,
) -> ExtractionResult | None:
    """v6 top-down extraction: 6 LLM calls + Python postprocessing + DB write."""

    # Call 1: 核心结论 + 关键理论
    step1 = await _v6_step1_anchors(content, platform, author, today, author_intent)
    if step1 is None:
        logger.warning(f"[v6] Step1 failed for RawPost id={raw_post.id}")
        return None

    if not step1.is_relevant_content:
        logger.info(f"[v6] RawPost {raw_post.id} not relevant: {step1.skip_reason}")
        if not force:
            raw_post.is_processed = True
            raw_post.processed_at = _utcnow()
            session.add(raw_post)
            await session.commit()
            return ExtractionResult(
                is_relevant_content=False, skip_reason=step1.skip_reason,
            )
        logger.info("[v6] force=True, continuing")

    n_cc = len(step1.core_conclusions)
    n_kt = len(step1.key_theories)
    logger.info(f"[v6] Step1 done: {n_cc} core conclusions, {n_kt} key theories")

    # Call 2: 相关支撑实体
    starting_id = max(
        [cc.id for cc in step1.core_conclusions] +
        [kt.id for kt in step1.key_theories] +
        [-1]
    ) + 1
    step2 = await _v6_step2_supporting(
        content, step1.core_conclusions, step1.key_theories, starting_id,
    )
    if step2 is None:
        logger.warning(f"[v6] Step2 failed for RawPost id={raw_post.id}")
        return None

    logger.info(
        f"[v6] Step2 done: {len(step2.facts)} facts, "
        f"{len(step2.sub_conclusions)} sub_conclusions, "
        f"{len(step2.assumptions)} assumptions, "
        f"{len(step2.predictions)} predictions, "
        f"{len(step2.solutions)} solutions"
    )

    # Python: 合并为 TypedEntity 列表
    typed_entities = _combine_to_typed_entities(step1, step2)
    logger.info(f"[v6] Combined: {len(typed_entities)} typed entities")

    # Call 3: 抽象简化
    step3 = await _v6_step3_abstract(typed_entities)
    if step3 is None:
        logger.warning("[v6] Step3 (abstract) failed, using unrefined entities")
        step3_entities = typed_entities
    else:
        step3_entities = merge_llm_fields_by_position(
            typed_entities, step3.entities,
            update_fields={"claim", "summary", "verifiable_statement", "condition_text"},
        )
        logger.info(f"[v6] Step3 done: {len(step3_entities)} entities abstracted")

    # Call 4: 合并决策
    step4 = await _v6_step4_merge(step3_entities)
    if step4 is None:
        logger.warning("[v6] Step4 (merge) failed, skipping merge")
        merged_entities = step3_entities
        merge_log: list[str] = []
    else:
        merged_entities, merge_log = apply_merge_decisions(
            step3_entities, step4.merges,
        )
        logger.info(
            f"[v6] Step4 done: {len(merged_entities)} entities "
            f"({len(step4.merges)} merges applied)"
        )

    # Call 5: 建立关系
    step5 = await _v6_step5_relationships(merged_entities)
    if step5 is None:
        logger.warning("[v6] Step5 (relationships) failed")
        typed_edges: list[TypedEdge] = []
    else:
        typed_edges = step5.edges
        logger.info(f"[v6] Step5 done: {len(typed_edges)} edges")

    # Call 6: 叙事摘要
    core_conclusions_text = [
        e.claim for e in merged_entities if e.is_core and e.entity_type == "conclusion"
    ]
    sub_conclusions_text = [
        e.claim for e in merged_entities
        if e.entity_type == "conclusion" and not e.is_core
    ]
    key_facts_text = [e.claim for e in merged_entities if e.entity_type == "fact"]
    theory_texts = [e.claim for e in merged_entities if e.entity_type == "theory"]
    if theory_texts:
        sub_conclusions_text = sub_conclusions_text + [f"[理论] {t}" for t in theory_texts]

    article_summary: str | None = None
    step6 = await _v6_step6_summary(core_conclusions_text, sub_conclusions_text, key_facts_text)
    if step6:
        article_summary = step6
        logger.info(f"[v6] Summary: {article_summary!r}")
    else:
        logger.warning("[v6] Summary generation failed")

    # Python: 后处理
    merged_entities, typed_edges = _v6_postprocess(merged_entities, typed_edges)

    # DB Write
    _rp_id = raw_post.id
    for _tbl in (EntityRelationship, Limitation, Effect, Problem,
                 Solution, Theory, Prediction, Conclusion,
                 ImplicitCondition, Assumption, Fact):
        await session.exec(delete(_tbl).where(_tbl.raw_post_id == _rp_id))
    await session.flush()

    author_db = await get_or_create_author(session, raw_post)
    extraction_result = await _write_v6_entities(
        merged_entities, typed_edges, raw_post, author_db, session,
    )

    raw_post.is_processed = True
    raw_post.processed_at = _utcnow()
    if article_summary:
        raw_post.content_summary = article_summary
    session.add(raw_post)
    await session.flush()
    await session.commit()

    extraction_result.article_summary = article_summary
    return extraction_result


# LLM call wrappers

async def _v6_step1_anchors(
    content: str, platform: str, author: str, today: str,
    author_intent: str | None = None,
) -> TopDownAnchorsResult | None:
    from anchor.extract.prompts.financial import step1_anchors as p
    user_msg = p.build_user_message(content, platform, author, today, author_intent)
    raw = await call_llm(p.SYSTEM, user_msg, _V6_STEP1_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, TopDownAnchorsResult, "v6-Step1")


async def _v6_step2_supporting(
    content: str, core_conclusions: list, key_theories: list, starting_id: int,
) -> SupportingScanResult | None:
    from anchor.extract.prompts.financial import step2_supporting as p
    user_msg = p.build_user_message(content, core_conclusions, key_theories, starting_id)
    raw = await call_llm(p.SYSTEM, user_msg, _V6_STEP2_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, SupportingScanResult, "v6-Step2")


async def _v6_step3_abstract(entities: list[TypedEntity]) -> AbstractedResult | None:
    from anchor.extract.prompts.financial import step3_abstract as p
    user_msg = p.build_user_message(entities)
    raw = await call_llm(p.SYSTEM, user_msg, _V6_STEP3_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, AbstractedResult, "v6-Step3")


async def _v6_step4_merge(entities: list[TypedEntity]) -> MergedResult | None:
    from anchor.extract.prompts.financial import step4_merge as p
    user_msg = p.build_user_message(entities)
    raw = await call_llm(p.SYSTEM, user_msg, _V6_STEP4_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, MergedResult, "v6-Step4")


async def _v6_step5_relationships(entities: list[TypedEntity]) -> RelationshipResult | None:
    from anchor.extract.prompts.financial import step5_relationships as p
    user_msg = p.build_user_message(entities)
    raw = await call_llm(p.SYSTEM, user_msg, _V6_STEP5_TOKENS)
    if raw is None:
        return None
    return parse_json(raw, RelationshipResult, "v6-Step5")


async def _v6_step6_summary(
    core_conclusions: list[str], sub_conclusions: list[str], key_facts: list[str],
) -> str | None:
    from pydantic import BaseModel as _BM

    class _SummaryResult(_BM):
        summary: str

    from anchor.extract.prompts.financial import step6_summary as p
    user_msg = p.build_user_message(core_conclusions, sub_conclusions, key_facts)
    raw = await call_llm(p.SYSTEM, user_msg, _SUMMARY_TOKENS)
    if raw is None:
        return None
    parsed = parse_json(raw, _SummaryResult, "v6-Step6")
    return parsed.summary if parsed else None


# Python helpers

def _combine_to_typed_entities(
    step1: TopDownAnchorsResult,
    step2: SupportingScanResult,
) -> list[TypedEntity]:
    entities: list[TypedEntity] = []

    for cc in step1.core_conclusions:
        entities.append(TypedEntity(
            id=cc.id, entity_type="conclusion",
            claim=cc.claim, summary=cc.summary, is_core=True,
            verifiable_statement=cc.verifiable_statement,
            author_confidence=cc.author_confidence,
        ))

    for kt in step1.key_theories:
        entities.append(TypedEntity(
            id=kt.id, entity_type="theory",
            claim=kt.claim, summary=kt.summary, is_core=True,
        ))

    for sf in step2.facts:
        entities.append(TypedEntity(
            id=sf.id, entity_type="fact",
            claim=sf.claim, summary=sf.summary,
            verifiable_statement=sf.verifiable_statement,
            temporal_type=sf.temporal_type, temporal_note=sf.temporal_note,
        ))

    for sc in step2.sub_conclusions:
        entities.append(TypedEntity(
            id=sc.id, entity_type="conclusion",
            claim=sc.claim, summary=sc.summary,
            verifiable_statement=sc.verifiable_statement,
            author_confidence=sc.author_confidence,
        ))

    for sa in step2.assumptions:
        entities.append(TypedEntity(
            id=sa.id, entity_type="assumption",
            claim=sa.condition_text, summary=sa.summary,
            condition_text=sa.condition_text,
            verifiable_statement=sa.verifiable_statement,
        ))

    for sp in step2.predictions:
        entities.append(TypedEntity(
            id=sp.id, entity_type="prediction",
            claim=sp.claim, summary=sp.summary,
            temporal_note=sp.temporal_note, author_confidence=sp.author_confidence,
        ))

    for ss in step2.solutions:
        entities.append(TypedEntity(
            id=ss.id, entity_type="solution",
            claim=ss.claim, summary=ss.summary,
            action_type=ss.action_type, action_target=ss.action_target,
            action_rationale=ss.action_rationale,
        ))

    for sp in step2.problems:
        entities.append(TypedEntity(
            id=sp.id, entity_type="problem",
            claim=sp.claim, summary=sp.summary,
            problem_domain=sp.problem_domain,
        ))

    for se in step2.effects:
        entities.append(TypedEntity(
            id=se.id, entity_type="effect",
            claim=se.claim, summary=se.summary,
            effect_type=se.effect_type,
        ))

    for sl in step2.limitations:
        entities.append(TypedEntity(
            id=sl.id, entity_type="limitation",
            claim=sl.claim, summary=sl.summary,
        ))

    return entities


def _v6_postprocess(
    entities: list[TypedEntity],
    edges: list[TypedEdge],
) -> tuple[list[TypedEntity], list[TypedEdge]]:
    """后处理：验证 edge_type、Theory cap、is_core 标记。"""

    entity_map = {e.id: e for e in entities}
    valid_entity_ids = set(entity_map.keys())
    valid_edges: list[TypedEdge] = []

    for edge in edges:
        if edge.source_id not in valid_entity_ids or edge.target_id not in valid_entity_ids:
            continue
        if edge.source_id == edge.target_id:
            continue

        src_type = entity_map[edge.source_id].entity_type
        tgt_type = entity_map[edge.target_id].entity_type
        expected = derive_edge_type(src_type, tgt_type)

        if edge.edge_type != expected:
            logger.debug(
                f"[v6] Edge type corrected: {edge.edge_type} → {expected} "
                f"({src_type}→{tgt_type})"
            )
            edge = TypedEdge(
                source_id=edge.source_id, target_id=edge.target_id, edge_type=expected,
            )
        valid_edges.append(edge)

    # Theory cap = 2
    MAX_THEORIES = 2
    _THEORY_KEYWORDS = {"理论", "模型", "原则", "周期", "框架", "Theory", "Model", "Cycle"}
    theory_entities = [e for e in entities if e.entity_type == "theory"]
    if len(theory_entities) > MAX_THEORIES:
        def _score(e: TypedEntity) -> int:
            return sum(1 for kw in _THEORY_KEYWORDS if kw in e.claim)
        ranked = sorted(theory_entities, key=_score, reverse=True)
        keep_ids = {e.id for e in ranked[:MAX_THEORIES]}
        for e_idx, e in enumerate(entities):
            if e.entity_type == "theory" and e.id not in keep_ids:
                entities[e_idx] = TypedEntity(
                    **{**e.model_dump(), "entity_type": "conclusion", "author_confidence": "likely"}
                )
                logger.debug(f"[v6] Theory {e.id} → conclusion (cap exceeded)")

    # is_core_conclusion 标记
    premise_conclusion_ids: set[int] = set()
    for edge in valid_edges:
        if edge.edge_type == "conclusion_supports_conclusion":
            premise_conclusion_ids.add(edge.source_id)
    for e_idx, e in enumerate(entities):
        if e.entity_type == "conclusion":
            should_be_core = e.id not in premise_conclusion_ids
            if e.is_core != should_be_core:
                entities[e_idx] = TypedEntity(**{**e.model_dump(), "is_core": should_be_core})

    return entities, valid_edges


async def _write_v6_entities(
    entities: list[TypedEntity],
    edges: list[TypedEdge],
    raw_post: RawPost,
    author_db: Author,
    session: AsyncSession,
) -> ExtractionResult:
    """将 v6 最终实体和边写入数据库，返回 ExtractionResult。"""

    all_id_maps: dict[str, dict[int, int]] = defaultdict(dict)

    facts_list: list[ExtractedFact] = []
    assumptions_list: list[ExtractedAssumption] = []
    conclusions_list: list[ExtractedConclusion] = []
    predictions_list: list[ExtractedPrediction] = []
    solutions_list: list[ExtractedSolution] = []
    theories_list: list[ExtractedTheory] = []
    problems_list: list[ExtractedProblem] = []
    effects_list: list[ExtractedEffect] = []
    limitations_list: list[ExtractedLimitation] = []
    conclusion_db_ids: list[int] = []

    for ent in entities:
        et = ent.entity_type

        if et == "fact":
            _ttype = ent.temporal_type if ent.temporal_type in ("retrospective", "predictive") else "retrospective"
            db_obj = Fact(
                raw_post_id=raw_post.id, summary=ent.summary, claim=ent.claim,
                verifiable_statement=ent.verifiable_statement or ent.claim,
                temporal_type=_ttype,
                temporal_note=ent.temporal_note,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["fact"][ent.id] = db_obj.id
            facts_list.append(ExtractedFact(
                summary=ent.summary, claim=ent.claim,
                verifiable_statement=ent.verifiable_statement or ent.claim,
                temporal_type=_ttype,
                temporal_note=ent.temporal_note,
            ))

        elif et == "assumption":
            db_obj = Assumption(
                raw_post_id=raw_post.id, summary=ent.summary,
                condition_text=ent.condition_text or ent.claim,
                verifiable_statement=ent.verifiable_statement,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["assumption"][ent.id] = db_obj.id
            assumptions_list.append(ExtractedAssumption(
                summary=ent.summary, condition_text=ent.condition_text or ent.claim,
                verifiable_statement=ent.verifiable_statement,
            ))

        elif et == "conclusion":
            confidence = normalize_confidence(ent.author_confidence)
            db_obj = Conclusion(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
                verifiable_statement=ent.verifiable_statement or ent.claim,
                author_confidence=confidence,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["conclusion"][ent.id] = db_obj.id
            conclusion_db_ids.append(db_obj.id)
            conclusions_list.append(ExtractedConclusion(
                summary=ent.summary, claim=ent.claim,
                verifiable_statement=ent.verifiable_statement or ent.claim,
                author_confidence=confidence,
            ))

        elif et == "prediction":
            confidence = normalize_confidence(ent.author_confidence)
            temporal_validity = "has_timeframe" if ent.temporal_note else "no_timeframe"
            db_obj = Prediction(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
                temporal_note=ent.temporal_note, temporal_validity=temporal_validity,
                author_confidence=confidence,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["prediction"][ent.id] = db_obj.id
            predictions_list.append(ExtractedPrediction(
                summary=ent.summary, claim=ent.claim,
                temporal_note=ent.temporal_note, author_confidence=confidence,
            ))

        elif et == "solution":
            db_obj = Solution(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
                action_type=ent.action_type, action_target=ent.action_target,
                action_rationale=ent.action_rationale,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["solution"][ent.id] = db_obj.id
            solutions_list.append(ExtractedSolution(
                summary=ent.summary, claim=ent.claim,
                action_type=ent.action_type, action_target=ent.action_target,
                action_rationale=ent.action_rationale,
            ))

        elif et == "theory":
            db_obj = Theory(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["theory"][ent.id] = db_obj.id
            theories_list.append(ExtractedTheory(summary=ent.summary, claim=ent.claim))

        elif et == "problem":
            db_obj = Problem(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
                problem_domain=ent.problem_domain,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["problem"][ent.id] = db_obj.id
            problems_list.append(ExtractedProblem(
                summary=ent.summary, claim=ent.claim,
                problem_domain=ent.problem_domain,
            ))

        elif et == "effect":
            db_obj = Effect(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
                effect_type=ent.effect_type,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["effect"][ent.id] = db_obj.id
            effects_list.append(ExtractedEffect(
                summary=ent.summary, claim=ent.claim,
                effect_type=ent.effect_type,
            ))

        elif et == "limitation":
            db_obj = Limitation(
                raw_post_id=raw_post.id, author_id=author_db.id,
                summary=ent.summary, claim=ent.claim,
            )
            session.add(db_obj)
            await session.flush()
            all_id_maps["limitation"][ent.id] = db_obj.id
            limitations_list.append(ExtractedLimitation(
                summary=ent.summary, claim=ent.claim,
            ))

    # Write edges
    premise_conclusion_ids: set[int] = set()
    conclusion_adj: dict[int, list[int]] = defaultdict(list)

    for edge in edges:
        src_ent = {e.id: e for e in entities}.get(edge.source_id)
        tgt_ent = {e.id: e for e in entities}.get(edge.target_id)
        if src_ent is None or tgt_ent is None:
            continue

        src_db_id = all_id_maps.get(src_ent.entity_type, {}).get(edge.source_id)
        tgt_db_id = all_id_maps.get(tgt_ent.entity_type, {}).get(edge.target_id)
        if src_db_id is None or tgt_db_id is None:
            continue

        db_rel = EntityRelationship(
            raw_post_id=raw_post.id,
            source_type=src_ent.entity_type, source_id=src_db_id,
            target_type=tgt_ent.entity_type, target_id=tgt_db_id,
            edge_type=edge.edge_type,
        )
        session.add(db_rel)

        if edge.edge_type == "conclusion_supports_conclusion":
            premise_conclusion_ids.add(src_db_id)
            conclusion_adj[src_db_id].append(tgt_db_id)

    await session.flush()

    # is_core_conclusion
    from sqlmodel import select
    from anchor.models import Conclusion as ConcModel
    for db_id in conclusion_db_ids:
        if db_id not in premise_conclusion_ids:
            conc_r = await session.exec(select(ConcModel).where(ConcModel.id == db_id))
            conc = conc_r.first()
            if conc:
                conc.is_core_conclusion = True
                session.add(conc)

    await session.flush()

    # Cycle detection
    cycle_ids = find_cycle_nodes(conclusion_db_ids, conclusion_adj)
    if cycle_ids:
        logger.warning(f"[v6] Detected {len(cycle_ids)} conclusion(s) in cycle: {cycle_ids}")
        for db_id in cycle_ids:
            conc_r = await session.exec(select(ConcModel).where(ConcModel.id == db_id))
            conc = conc_r.first()
            if conc:
                conc.is_in_cycle = True
                session.add(conc)

    n_core = len(conclusion_db_ids) - len(
        premise_conclusion_ids.intersection(set(conclusion_db_ids))
    )
    logger.info(
        f"[v6] RawPost {raw_post.id} processed: "
        f"{len(facts_list)} facts, {len(assumptions_list)} assumptions, "
        f"{len(conclusions_list)} conclusions (core={n_core}), "
        f"{len(predictions_list)} predictions, {len(solutions_list)} solutions, "
        f"{len(theories_list)} theories, {len(problems_list)} problems, "
        f"{len(effects_list)} effects, {len(limitations_list)} limitations, "
        f"{len(edges)} edges"
    )

    return ExtractionResult(
        is_relevant_content=True,
        facts=facts_list, assumptions=assumptions_list, conclusions=conclusions_list,
        predictions=predictions_list, solutions=solutions_list, theories=theories_list,
        problems=problems_list, effects=effects_list, limitations=limitations_list,
    )
