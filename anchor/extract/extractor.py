"""
Layer2 — 观点提取器（v4 — 六实体）
====================================
输入：一条原始帖子（RawPost）
输出：写入数据库的六实体 + EntityRelationship 边记录

写库顺序（Relationship 依赖前序实体的 DB ID）：
  1. 批量创建 Fact → fact_id_map
  2. 批量创建 Assumption → assumption_id_map
  3. 批量创建 ImplicitCondition → implicit_id_map
  4. 批量创建 Conclusion → conclusion_id_map
  5. 批量创建 Prediction → prediction_id_map
  6. 批量创建 Solution → solution_id_map
  7. 创建 EntityRelationship 边
  8. DAG 分析：识别核心结论（is_core_conclusion）
  9. DFS 循环检测：标记 is_in_cycle
  10. 标注 Prediction.temporal_validity
  11. 标记 RawPost.is_processed
"""

from __future__ import annotations

import datetime
import json
import re
from collections import defaultdict

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.prompts import PROMPT_REGISTRY, DEFAULT_PROMPT_VERSION
from anchor.extract.schemas import ExtractionResult
from anchor.llm_client import chat_completion
from anchor.models import (
    Assumption,
    Author,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    Prediction,
    RawPost,
    Solution,
    _utcnow,
)

_MAX_TOKENS = 8000


class Extractor:
    """观点提取器（v4 — 六实体）

    Usage:
        extractor = Extractor()
        result = await extractor.extract(raw_post, session)
    """

    def __init__(self, prompt_version: str | None = None) -> None:
        version = prompt_version or DEFAULT_PROMPT_VERSION
        if version not in PROMPT_REGISTRY:
            raise ValueError(
                f"未知的 prompt 版本：{version!r}。"
                f"可用版本：{list(PROMPT_REGISTRY.keys())}"
            )
        self._prompt = PROMPT_REGISTRY[version]
        logger.info(f"Extractor initialized with prompt version: {version}")

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def extract(
        self, raw_post: RawPost, session: AsyncSession
    ) -> ExtractionResult | None:
        """对一条帖子执行六实体提取，写入数据库，返回提取结果。"""
        if raw_post.is_processed:
            logger.debug(f"RawPost {raw_post.id} already processed, skipping")
            return None

        if raw_post.is_duplicate:
            logger.info(
                f"RawPost {raw_post.id} is a cross-platform duplicate, skipping extraction"
            )
            return None

        content = raw_post.enriched_content or raw_post.content

        # 追加图片描述（若帖子含图片且视觉模型可用）
        if raw_post.media_json:
            from anchor.collect.media_describer import describe_media
            media_desc = await describe_media(raw_post)
            if media_desc:
                content = content + "\n\n--- 图片内容 ---\n" + media_desc

        today = (raw_post.posted_at or datetime.datetime.utcnow()).date().isoformat()
        user_msg = self._prompt.build_user_message(
            content=content,
            platform=raw_post.source,
            author=raw_post.author_name,
            today=today,
        )

        logger.info(f"Extracting from RawPost id={raw_post.id} (prompt={self.prompt_version})")

        raw_json = await self._call_llm(user_msg)
        if raw_json is None:
            return None

        result = _parse_extraction(raw_json)
        if result is None:
            logger.warning(f"Failed to parse LLM output for RawPost id={raw_post.id}")
            return None

        if not result.is_relevant_content:
            logger.info(f"RawPost {raw_post.id} skipped: {result.skip_reason}")
            raw_post.is_processed = True
            raw_post.processed_at = _utcnow()
            session.add(raw_post)
            await session.commit()
            return result

        author = await _get_or_create_author(session, raw_post)

        # ── Step 1：创建所有 Fact ─────────────────────────────────────────
        fact_id_map: dict[int, int] = {}
        for idx, ef in enumerate(result.facts):
            db_fact = Fact(
                raw_post_id=raw_post.id,
                claim=ef.claim,
                verifiable_statement=ef.verifiable_statement,
                temporal_type=ef.temporal_type,
                temporal_note=ef.temporal_note,
            )
            session.add(db_fact)
            await session.flush()
            fact_id_map[idx] = db_fact.id

        # ── Step 2：创建 Assumption ──────────────────────────────────────
        assumption_id_map: dict[int, int] = {}
        for idx, ea in enumerate(result.assumptions):
            db_assump = Assumption(
                raw_post_id=raw_post.id,
                condition_text=ea.condition_text,
                verifiable_statement=ea.verifiable_statement,
                temporal_note=ea.temporal_note,
            )
            session.add(db_assump)
            await session.flush()
            assumption_id_map[idx] = db_assump.id

        # ── Step 3：创建 ImplicitCondition ──────────────────────────────
        implicit_id_map: dict[int, int] = {}
        for idx, ei in enumerate(result.implicit_conditions):
            db_impl = ImplicitCondition(
                raw_post_id=raw_post.id,
                condition_text=ei.condition_text,
                is_obvious_consensus=ei.is_obvious_consensus,
            )
            session.add(db_impl)
            await session.flush()
            implicit_id_map[idx] = db_impl.id

        # ── Step 4：创建 Conclusion ──────────────────────────────────────
        conclusion_id_map: dict[int, int] = {}
        conclusion_db_ids: list[int] = []
        for idx, ec in enumerate(result.conclusions):
            db_conc = Conclusion(
                raw_post_id=raw_post.id,
                author_id=author.id,
                claim=ec.claim,
                verifiable_statement=ec.verifiable_statement,
                author_confidence=ec.author_confidence,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_id_map[idx] = db_conc.id
            conclusion_db_ids.append(db_conc.id)

        # ── Step 5：创建 Prediction ──────────────────────────────────────
        prediction_id_map: dict[int, int] = {}
        for idx, ep in enumerate(result.predictions):
            temporal_validity = "has_timeframe" if ep.temporal_note else "no_timeframe"
            db_pred = Prediction(
                raw_post_id=raw_post.id,
                author_id=author.id,
                claim=ep.claim,
                temporal_note=ep.temporal_note,
                temporal_validity=temporal_validity,
                author_confidence=ep.author_confidence,
            )
            session.add(db_pred)
            await session.flush()
            prediction_id_map[idx] = db_pred.id

        # ── Step 6：创建 Solution ────────────────────────────────────────
        solution_id_map: dict[int, int] = {}
        for idx, es in enumerate(result.solutions):
            db_sol = Solution(
                raw_post_id=raw_post.id,
                author_id=author.id,
                claim=es.claim,
                action_type=es.action_type,
                action_target=es.action_target,
                action_rationale=es.action_rationale,
            )
            session.add(db_sol)
            await session.flush()
            solution_id_map[idx] = db_sol.id

        # ── Step 7：创建 EntityRelationship 边 ───────────────────────────
        # ID maps for each entity type（兼容 LLM 有时返回复数形式，如 implicit_conditions）
        entity_maps = {
            "fact": fact_id_map,
            "facts": fact_id_map,
            "assumption": assumption_id_map,
            "assumptions": assumption_id_map,
            "implicit_condition": implicit_id_map,
            "implicit_conditions": implicit_id_map,
            "conclusion": conclusion_id_map,
            "conclusions": conclusion_id_map,
            "prediction": prediction_id_map,
            "predictions": prediction_id_map,
            "solution": solution_id_map,
            "solutions": solution_id_map,
        }

        # Track which conclusions are used as sub-conclusions (premise_conclusion_ids)
        premise_conclusion_ids: set[int] = set()

        for er in result.relationships:
            src_map = entity_maps.get(er.source_type, {})
            tgt_map = entity_maps.get(er.target_type, {})
            src_id = src_map.get(er.source_index)
            tgt_id = tgt_map.get(er.target_index)

            if src_id is None or tgt_id is None:
                logger.warning(
                    f"Relationship edge skipped: {er.source_type}[{er.source_index}] "
                    f"→ {er.target_type}[{er.target_index}] (id not found)"
                )
                continue

            db_rel = EntityRelationship(
                raw_post_id=raw_post.id,
                source_type=er.source_type,
                source_id=src_id,
                target_type=er.target_type,
                target_id=tgt_id,
                edge_type=er.edge_type,
                note=er.note,
            )
            session.add(db_rel)

            # Track premise conclusions for DAG analysis
            if er.edge_type == "conclusion_supports_conclusion" and er.source_type == "conclusion":
                premise_conclusion_ids.add(src_id)

        await session.flush()

        # ── Step 8：识别核心结论（is_core_conclusion）────────────────────
        # 核心结论 = 不被任何其他结论用作前提的结论
        for db_id in conclusion_db_ids:
            is_core = db_id not in premise_conclusion_ids
            if is_core:
                conc_r = await session.exec(
                    select(Conclusion).where(Conclusion.id == db_id)
                )
                conc = conc_r.first()
                if conc:
                    conc.is_core_conclusion = True
                    session.add(conc)

        await session.flush()

        # ── Step 9：检测结论环（is_in_cycle）────────────────────────────
        # 构建结论→结论有向图
        adj: dict[int, list[int]] = defaultdict(list)
        for er in result.relationships:
            if er.edge_type == "conclusion_supports_conclusion":
                src_id = conclusion_id_map.get(er.source_index)
                tgt_id = conclusion_id_map.get(er.target_index)
                if src_id is not None and tgt_id is not None:
                    adj[src_id].append(tgt_id)

        cycle_ids = _find_cycle_nodes(conclusion_db_ids, adj)
        if cycle_ids:
            logger.warning(
                f"Detected {len(cycle_ids)} conclusion(s) in logic cycle: {cycle_ids}"
            )
            for db_id in cycle_ids:
                conc_r = await session.exec(
                    select(Conclusion).where(Conclusion.id == db_id)
                )
                conc = conc_r.first()
                if conc:
                    conc.is_in_cycle = True
                    session.add(conc)

        # ── Step 10：标记 RawPost 已处理 ────────────────────────────────
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        await session.commit()

        logger.info(
            f"RawPost {raw_post.id} processed: "
            f"{len(result.facts)} facts, "
            f"{len(result.assumptions)} assumptions, "
            f"{len(result.implicit_conditions)} implicit_conditions, "
            f"{len(result.conclusions)} conclusions "
            f"(core={len(conclusion_db_ids) - len(premise_conclusion_ids.intersection(conclusion_db_ids))}), "
            f"{len(result.predictions)} predictions, "
            f"{len(result.solutions)} solutions, "
            f"{len(result.relationships)} relationships"
        )
        return result

    async def _call_llm(self, user_message: str) -> str | None:
        resp = await chat_completion(
            system=self._prompt.system_prompt,
            user=user_message,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            return None
        logger.debug(
            f"LLM usage: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}"
        )
        return resp.content


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_extraction(raw: str) -> ExtractionResult | None:
    """从 LLM 返回文本中提取 JSON 并解析为 ExtractionResult。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()

    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        return ExtractionResult.model_validate(data)
    except Exception as exc:
        logger.warning(f"JSON parse/validation error: {exc}\nRaw: {raw[:500]}")
        return None


def _find_cycle_nodes(all_ids: list[int], adj: dict[int, list[int]]) -> set[int]:
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


async def _get_or_create_author(session: AsyncSession, raw_post: RawPost) -> Author:
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
