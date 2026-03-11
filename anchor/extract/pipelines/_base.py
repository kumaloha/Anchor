"""
pipelines/_base.py — 共享工具方法
===================================
LLM 调用封装、JSON 解析、边类型推导等纯工具函数。
不含任何 pipeline 业务逻辑。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Author, RawPost
from anchor.extract.schemas import (
    ClassifiedEntity,
    ExtractedFact,
    ExtractedAssumption,
    ExtractedConclusion,
    ExtractedPrediction,
    ExtractedSolution,
    ExtractedTheory,
    ExtractedProblem,
    ExtractedEffect,
    ExtractedLimitation,
    ExtractedImplicitCondition,
    ExtractedRelationship,
    ExtractionResult,
    ImplicitConditionItem,
    MergeGroup,
    RawClaim,
    RawEdge,
    TypedEntity,
    TypedEdge,
    MergeDecision,
)


async def call_llm(system: str, user: str, max_tokens: int) -> str | None:
    resp = await chat_completion(system=system, user=user, max_tokens=max_tokens)
    if resp is None:
        return None
    logger.debug(f"LLM: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}")
    return resp.content


def parse_json(raw: str, model_cls, step_name: str):
    """从 LLM 返回文本中提取 JSON 并解析为给定 Pydantic 模型。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()

    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning(f"{step_name}: no JSON found in output")
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        return model_cls.model_validate(data)
    except Exception as exc:
        logger.warning(f"{step_name} parse error: {exc}\nRaw: {raw[:400]}")
        return None


def apply_merges(
    claims: List[RawClaim],
    edges: List[RawEdge],
    merges: List[MergeGroup],
) -> tuple[List[RawClaim], List[RawEdge]]:
    """应用合并组：将 discard 节点的所有边重定向到 keep 节点，去重，更新 summary。"""
    remap: dict[int, int] = {}
    summary_update: dict[int, str] = {}
    for mg in merges:
        for d in mg.discard:
            remap[d] = mg.keep
        summary_update[mg.keep] = mg.merged_summary

    text_update: dict[int, str] = {}
    for mg in merges:
        if mg.merged_text:
            text_update[mg.keep] = mg.merged_text

    discard_ids = set(remap.keys())
    new_claims = []
    for c in claims:
        if c.id in discard_ids:
            continue
        new_text = text_update.get(c.id, c.text)
        new_summary = summary_update.get(c.id, c.summary)
        if new_text != c.text or new_summary != c.summary:
            c = RawClaim(id=c.id, text=new_text, summary=new_summary)
        new_claims.append(c)

    seen_edges: set[tuple[int, int]] = set()
    new_edges = []
    for e in edges:
        from_id = remap.get(e.from_id, e.from_id)
        to_id = remap.get(e.to_id, e.to_id)
        if from_id == to_id:
            continue
        key = (from_id, to_id)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        new_edges.append(RawEdge(from_id=from_id, to_id=to_id))

    return new_claims, new_edges


def get_entity_type(claim_id: int, class_map: dict[int, ClassifiedEntity]) -> str | None:
    ce = class_map.get(claim_id)
    if ce is None:
        return None
    return ce.entity_type


def derive_edge_type(src_type: str, tgt_type: str) -> str:
    """根据源/目标实体类型推导边类型。"""
    if src_type == "implicit_condition":
        return "implicit_conditions_conclusion"
    if src_type == "assumption":
        return "assumption_conditions_conclusion"
    if src_type == "fact":
        if tgt_type == "theory":
            return "fact_supports_theory"
        if tgt_type == "problem":
            return "fact_supports_problem"
        return "fact_supports_conclusion"
    if src_type == "conclusion":
        if tgt_type == "conclusion":
            return "conclusion_supports_conclusion"
        if tgt_type == "prediction":
            return "conclusion_leads_to_prediction"
        if tgt_type == "solution":
            return "conclusion_enables_solution"
        if tgt_type == "theory":
            return "conclusion_supports_theory"
        if tgt_type == "problem":
            return "conclusion_identifies_problem"
    if src_type == "theory":
        if tgt_type == "theory":
            return "theory_supports_theory"
        if tgt_type == "conclusion":
            return "theory_supports_conclusion"
        if tgt_type == "prediction":
            return "theory_leads_to_prediction"
        if tgt_type == "solution":
            return "theory_enables_solution"
    if src_type == "prediction" and tgt_type == "solution":
        return "conclusion_enables_solution"
    # 问题-解法-效果-局限 链路
    if src_type == "problem":
        if tgt_type == "solution":
            return "problem_leads_to_solution"
        if tgt_type == "conclusion":
            return "problem_leads_to_conclusion"
    if src_type == "solution" and tgt_type == "effect":
        return "solution_produces_effect"
    if src_type == "effect" and tgt_type == "limitation":
        return "effect_has_limitation"
    if src_type == "solution" and tgt_type == "limitation":
        return "solution_has_limitation"
    return "fact_supports_conclusion"


_VALID_CONFIDENCE = {"certain", "likely", "uncertain", "speculative"}


def normalize_confidence(val: Optional[str]) -> Optional[str]:
    if val is None or val in _VALID_CONFIDENCE:
        return val
    return "uncertain"


def find_cycle_nodes(all_ids: list[int], adj: dict[int, list[int]]) -> set[int]:
    """DFS 检测有向图中的环节点，返回在环中的节点 ID 集合。"""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {nid: WHITE for nid in all_ids}
    in_cycle: set[int] = set()
    stack: list[int] = []

    def dfs(v: int) -> None:
        color[v] = GRAY
        stack.append(v)
        for w in adj.get(v, []):
            if color.get(w, BLACK) == GRAY:
                cycle_start = stack.index(w)
                for node in stack[cycle_start:]:
                    in_cycle.add(node)
            elif color.get(w, BLACK) == WHITE:
                dfs(w)
        stack.pop()
        color[v] = BLACK

    for nid in all_ids:
        if color.get(nid, BLACK) == WHITE:
            dfs(nid)

    return in_cycle


async def get_or_create_author(session: AsyncSession, raw_post: RawPost) -> Author:
    if raw_post.author_platform_id:
        result = await session.exec(
            select(Author).where(
                Author.platform == raw_post.source,
                Author.platform_id == raw_post.author_platform_id,
            )
        )
        author = result.first()
        if author:
            return author

    author = Author(
        name=raw_post.author_name,
        platform=raw_post.source,
        platform_id=raw_post.author_platform_id,
        profile_url=f"https://{raw_post.source}.com/{raw_post.author_platform_id}",
    )
    session.add(author)
    await session.flush()
    return author


def build_extraction_result_from_claims(
    claims: List[RawClaim],
    edges: List[RawEdge],
    class_map: dict[int, ClassifiedEntity],
    implicit_items: List[ImplicitConditionItem],
) -> dict:
    """将分类后的声明组织为按实体类型分组的字典（claim_id → Extracted* 对象）。"""
    facts: dict[int, ExtractedFact] = {}
    assumptions: dict[int, ExtractedAssumption] = {}
    conclusions: dict[int, ExtractedConclusion] = {}
    predictions: dict[int, ExtractedPrediction] = {}
    solutions: dict[int, ExtractedSolution] = {}
    theories: dict[int, ExtractedTheory] = {}
    problems: dict[int, ExtractedProblem] = {}
    effects: dict[int, ExtractedEffect] = {}
    limitations: dict[int, ExtractedLimitation] = {}

    for c in claims:
        ce = class_map.get(c.id)
        if ce is None:
            facts[c.id] = ExtractedFact(
                summary=c.summary, claim=c.text, verifiable_statement=c.text,
            )
            continue

        et = ce.entity_type
        if et == "fact":
            facts[c.id] = ExtractedFact(
                summary=c.summary, claim=c.text,
                verifiable_statement=ce.verifiable_statement or c.text,
            )
        elif et == "assumption":
            assumptions[c.id] = ExtractedAssumption(
                summary=c.summary, condition_text=c.text, temporal_note=ce.temporal_note,
            )
        elif et == "conclusion":
            conclusions[c.id] = ExtractedConclusion(
                summary=c.summary, claim=c.text,
                verifiable_statement=ce.verifiable_statement or c.text,
                author_confidence=normalize_confidence(ce.author_confidence),
            )
        elif et == "prediction":
            predictions[c.id] = ExtractedPrediction(
                summary=c.summary, claim=c.text,
                temporal_note=ce.temporal_note,
                author_confidence=normalize_confidence(ce.author_confidence),
            )
        elif et == "solution":
            solutions[c.id] = ExtractedSolution(
                summary=c.summary, claim=c.text,
                action_type=ce.action_type, action_target=ce.action_target,
                action_rationale=ce.action_rationale,
            )
        elif et == "theory":
            theories[c.id] = ExtractedTheory(summary=c.summary, claim=c.text)
        elif et == "problem":
            problems[c.id] = ExtractedProblem(summary=c.summary, claim=c.text)
        elif et == "effect":
            effects[c.id] = ExtractedEffect(summary=c.summary, claim=c.text)
        elif et == "limitation":
            limitations[c.id] = ExtractedLimitation(summary=c.summary, claim=c.text)
        else:
            facts[c.id] = ExtractedFact(
                summary=c.summary, claim=c.text, verifiable_statement=c.text,
            )

    return {
        "facts": facts, "assumptions": assumptions, "conclusions": conclusions,
        "predictions": predictions, "solutions": solutions, "theories": theories,
        "problems": problems, "effects": effects, "limitations": limitations,
    }


def to_extraction_result(
    result: dict,
    implicit_items: List[ImplicitConditionItem],
    edges: List[RawEdge],
    class_map: dict[int, ClassifiedEntity],
) -> ExtractionResult:
    """将内部 dict 结构转换为标准 ExtractionResult。"""
    facts = list(result.get("facts", {}).values())
    assumptions = list(result.get("assumptions", {}).values())
    conclusions = list(result.get("conclusions", {}).values())
    predictions = list(result.get("predictions", {}).values())
    solutions = list(result.get("solutions", {}).values())
    theories = list(result.get("theories", {}).values())
    problems = list(result.get("problems", {}).values())
    effects = list(result.get("effects", {}).values())
    limitations = list(result.get("limitations", {}).values())

    implicit_conditions = [
        ExtractedImplicitCondition(
            summary=item.summary,
            condition_text=item.condition_text,
            is_obvious_consensus=item.is_obvious_consensus,
        )
        for item in implicit_items
    ]

    fact_ids = list(result.get("facts", {}).keys())
    assumption_ids = list(result.get("assumptions", {}).keys())
    conclusion_ids = list(result.get("conclusions", {}).keys())
    prediction_ids = list(result.get("predictions", {}).keys())
    solution_ids = list(result.get("solutions", {}).keys())
    theory_ids = list(result.get("theories", {}).keys())
    problem_ids = list(result.get("problems", {}).keys())
    effect_ids = list(result.get("effects", {}).keys())
    limitation_ids = list(result.get("limitations", {}).keys())

    def get_type_and_index(claim_id: int) -> tuple[str, int] | None:
        ce = class_map.get(claim_id)
        et = ce.entity_type if ce else "fact"
        for id_list, type_name in [
            (fact_ids, "fact"), (assumption_ids, "assumption"),
            (conclusion_ids, "conclusion"), (prediction_ids, "prediction"),
            (solution_ids, "solution"), (theory_ids, "theory"),
            (problem_ids, "problem"), (effect_ids, "effect"),
            (limitation_ids, "limitation"),
        ]:
            if claim_id in id_list:
                return type_name, id_list.index(claim_id)
        return None

    relationships = []
    for e in edges:
        src = get_type_and_index(e.from_id)
        tgt = get_type_and_index(e.to_id)
        if src and tgt:
            relationships.append(
                ExtractedRelationship(
                    source_type=src[0], source_index=src[1],
                    target_type=tgt[0], target_index=tgt[1],
                    edge_type=derive_edge_type(src[0], tgt[0]),
                )
            )

    return ExtractionResult(
        is_relevant_content=True,
        facts=facts, assumptions=assumptions, implicit_conditions=implicit_conditions,
        conclusions=conclusions, predictions=predictions, solutions=solutions,
        theories=theories, problems=problems, effects=effects, limitations=limitations,
        relationships=relationships,
    )


def merge_llm_fields_by_position(
    originals: list[TypedEntity],
    updated: list[TypedEntity],
    update_fields: set[str],
) -> list[TypedEntity]:
    """LLM 重编号后按位置匹配，只取指定字段更新。"""
    result: list[TypedEntity] = []
    for i, u in enumerate(updated):
        if i < len(originals):
            merged_data = originals[i].model_dump()
            for field in update_fields:
                merged_data[field] = getattr(u, field)
            result.append(TypedEntity(**merged_data))
        else:
            result.append(u)
    return result


def apply_merge_decisions(
    entities: list[TypedEntity],
    merges: list[MergeDecision],
) -> tuple[list[TypedEntity], list[str]]:
    """在 Python 中执行合并指令，保证结构字段不被 LLM 篡改。"""
    entity_map = {e.id: e for e in entities}
    removed_ids: set[int] = set()
    merge_log: list[str] = []

    for m in merges:
        keep = entity_map.get(m.keep_id)
        remove = entity_map.get(m.remove_id)
        if keep is None or remove is None:
            continue
        if keep.entity_type != remove.entity_type:
            continue
        if m.merged_claim:
            keep.claim = m.merged_claim
        if m.merged_summary:
            keep.summary = m.merged_summary
        if remove.is_core:
            keep.is_core = True
        removed_ids.add(m.remove_id)
        merge_log.append(
            f"merged #{m.keep_id}+#{m.remove_id} → #{m.keep_id}: {m.reason}"
        )

    result = [e for e in entities if e.id not in removed_ids]
    for i, e in enumerate(result):
        e.id = i
    return result, merge_log
