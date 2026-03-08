"""
Layer2 — 观点提取器（v5 — 六步多阶段流水线）
=============================================
输入：一条原始帖子（RawPost）
输出：写入数据库的六实体 + EntityRelationship 边记录

v5 流程（6步 + DB写库）：
  Step 1 [LLM] v5_step1_claims  — 原始声明提取 + 有向边
  Step 2 [LLM] v5_step2_merge   — 同义声明合并（claims <= 1 时跳过）
  Step 3 [Python]               — 构建 DAG，识别核心/孤立节点
  Step 4 [LLM] v5_step3_classify — 节点分类（全局 DAG 视角）
  Step 5 [LLM] v5_step4_implicit — 批量生成隐含条件（有入边才执行）
  Step 6 [Python]               — 推导边类型
  Step 7 [DB Write]             — 与 v4 相同写库顺序

写库顺序（Relationship 依赖前序实体的 DB ID）：
  1. 批量创建 Fact → fact_id_map
  2. 批量创建 Assumption → assumption_id_map
  3. 批量创建 ImplicitCondition → implicit_id_map
  4. 批量创建 Conclusion → conclusion_id_map
  5. 批量创建 Prediction → prediction_id_map
  6. 批量创建 Solution → solution_id_map
  7. 创建 EntityRelationship 边（含隐含条件边）
  8. DAG 分析：识别核心结论（is_core_conclusion）
  9. DFS 循环检测：标记 is_in_cycle
  10. 标记 RawPost.is_processed
"""

from __future__ import annotations

import datetime
import json
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.schemas import (
    ClassifiedEntity,
    ExtractionResult,
    ExtractedAssumption,
    ExtractedConclusion,
    ExtractedFact,
    ExtractedImplicitCondition,
    ExtractedPrediction,
    ExtractedRelationship,
    ExtractedSolution,
    ImplicitConditionItem,
    MergeGroup,
    RawClaim,
    RawEdge,
    PolicyChangeAnnotation,
    PolicyComparisonResult,
    PolicyExtractionResult,
    PolicyItem as PolicyItemSchema,
    Step1PolicyResult,
    Step1Result,
    Step2Result,
    Step3Result,
    Step4Result,
)
from pydantic import BaseModel as _BaseModel

from anchor.llm_client import chat_completion
from anchor.models import (
    Assumption,
    Author,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    Policy,
    PolicyItem,
    PolicyMeasure,
    PolicyTheme,
    Prediction,
    RawPost,
    Solution,
    _utcnow,
)


class _SummaryResult(_BaseModel):
    summary: str


_MAX_TOKENS = 6000
_STEP1_TOKENS = 4000
_STEP2_TOKENS = 2000
_STEP3_TOKENS = 4000
_STEP4_TOKENS = 3000
_STEP5_TOKENS = 1000


class Extractor:
    """观点提取器（v5 — 六步多阶段流水线）

    Usage:
        extractor = Extractor()
        result = await extractor.extract(raw_post, session)
    """

    def __init__(self) -> None:
        logger.info("Extractor initialized (v5 multi-step pipeline)")

    async def extract(
        self,
        raw_post: RawPost,
        session: AsyncSession,
        content_mode: str = "standard",
        author_intent: str | None = None,
        force: bool = False,
    ) -> ExtractionResult | None:
        """对一条帖子执行六实体提取，写入数据库，返回提取结果。

        Args:
            raw_post:      待处理的原始帖子
            session:       异步数据库 Session
            content_mode:  "standard"（默认六实体流水线）或 "policy"（政策模式）
            author_intent: Chain 2 前置分类预判的作者意图（注入 Step 1 提示词）
            force:         True 时跳过 is_processed / is_relevant_content 检查，
                           强制完整运行（用于手动 run_url.py）
        """
        if not force and raw_post.is_processed:
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
        platform = raw_post.source
        author = raw_post.author_name

        logger.info(f"[v5] Extracting RawPost id={raw_post.id}")

        # ── Policy 模式：独立流程 ─────────────────────────────────────────
        if content_mode == "policy":
            return await self._extract_policy(
                raw_post, session, content, platform, author, today,
                author_intent
            )

        # ── Step 1：原始声明提取 ──────────────────────────────────────────
        step1 = await self._step1_claims(content, platform, author, today, author_intent)
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
                return ExtractionResult(
                    is_relevant_content=False,
                    skip_reason=step1.skip_reason,
                )
            logger.info(f"[v5] force=True, continuing with empty claims")

        claims = step1.claims or []
        edges = step1.edges
        logger.info(f"[v5] Step1 done: {len(claims)} claims, {len(edges)} edges")

        # ── Step 2：同义声明合并 ─────────────────────────────────────────
        if len(claims) > 1:
            step2 = await self._step2_merge(claims)
            if step2 is not None and step2.merges:
                claims, edges = _apply_merges(claims, edges, step2.merges)
                logger.info(f"[v5] Step2 merge: {len(step2.merges)} groups → {len(claims)} claims remain")
            else:
                logger.info("[v5] Step2: no merges")
        else:
            logger.info("[v5] Step2: skipped (≤1 claim)")

        # ── Step 3：构建 DAG + 识别核心/孤立节点 ─────────────────────────
        claim_ids = {c.id for c in claims}
        in_degree: dict[int, int] = defaultdict(int)
        out_degree: dict[int, int] = defaultdict(int)
        for e in edges:
            if e.from_id in claim_ids and e.to_id in claim_ids:
                out_degree[e.from_id] += 1
                in_degree[e.to_id] += 1

        core_ids: Set[int] = {
            n for n in claim_ids
            if in_degree[n] > 0 and out_degree[n] == 0
        }
        isolated_ids: Set[int] = {
            n for n in claim_ids
            if in_degree[n] == 0 and out_degree[n] == 0
        }
        logger.info(f"[v5] DAG: core={len(core_ids)}, isolated={len(isolated_ids)}")

        # ── Step 4：节点分类 ──────────────────────────────────────────────
        step3 = await self._step3_classify(claims, edges, core_ids, isolated_ids)
        if step3 is None:
            logger.warning(f"[v5] Step3 (classify) failed for RawPost id={raw_post.id}")
            return None

        # 构建 claim_id → ClassifiedEntity 映射
        class_map: dict[int, ClassifiedEntity] = {
            ce.claim_id: ce for ce in step3.classifications
        }
        logger.info(f"[v5] Step3 done: {len(class_map)} classified")

        # ── Step 5：批量生成隐含条件 ──────────────────────────────────────
        implicit_items: List[ImplicitConditionItem] = []
        claims_by_id = {c.id: c for c in claims}

        # 收集所有有入边的推断（source_text → target_text, target_id）
        inferences: List[Tuple[str, str, int]] = []
        for e in edges:
            src = claims_by_id.get(e.from_id)
            tgt = claims_by_id.get(e.to_id)
            if src and tgt:
                inferences.append((src.text, tgt.text, tgt.id))

        if inferences:
            step4 = await self._step4_implicit(inferences)
            if step4 is not None:
                implicit_items = step4.implicit_conditions
                logger.info(f"[v5] Step4 done: {len(implicit_items)} implicit conditions")
        else:
            logger.info("[v5] Step4: skipped (no edges)")

        # ── Step 5b：叙事摘要生成 ─────────────────────────────────────────
        core_conclusions_text = [
            claims_by_id[cid].text
            for cid in core_ids
            if cid in claims_by_id
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
        article_summary: str | None = None
        step5 = await self._step5_summary(
            core_conclusions_text, sub_conclusions_text, key_facts_text
        )
        if step5:
            article_summary = step5
            logger.info(f"[v5] Step5 summary: {article_summary!r}")
        else:
            logger.warning("[v5] Step5 summary failed, skipping")

        # ── Step 6：推导边类型 ────────────────────────────────────────────
        # 先构建隐含条件到目标节点的映射（target_claim_id）
        # 隐含条件是在写库时才分配 DB id，先记录待写入的关系

        # 构建最终提取结果（分类后的实体列表）
        result = _build_extraction_result(
            claims=claims,
            edges=edges,
            class_map=class_map,
            implicit_items=implicit_items,
        )

        # ── Step 7：写库 ──────────────────────────────────────────────────
        author_db = await _get_or_create_author(session, raw_post)

        # 1. 创建 Fact
        fact_id_map: dict[int, int] = {}
        for claim_id, ef in result.get("facts", {}).items():
            db_fact = Fact(
                raw_post_id=raw_post.id,
                summary=ef.summary,
                claim=ef.claim,
                verifiable_statement=ef.verifiable_statement,
                temporal_type=ef.temporal_type,
                temporal_note=ef.temporal_note,
            )
            session.add(db_fact)
            await session.flush()
            fact_id_map[claim_id] = db_fact.id

        # 2. 创建 Assumption
        assumption_id_map: dict[int, int] = {}
        for claim_id, ea in result.get("assumptions", {}).items():
            db_assump = Assumption(
                raw_post_id=raw_post.id,
                summary=ea.summary,
                condition_text=ea.condition_text,
                verifiable_statement=ea.verifiable_statement,
                temporal_note=ea.temporal_note,
            )
            session.add(db_assump)
            await session.flush()
            assumption_id_map[claim_id] = db_assump.id

        # 3. 创建 ImplicitCondition
        # implicit_items 是列表，target_claim_id 指向哪个声明
        # 我们为每个 implicit item 分配一个 list index，后续用 target_claim_id 建边
        implicit_db_list: list[tuple[int, int]] = []  # (target_claim_id, db_id)
        for item in implicit_items:
            db_impl = ImplicitCondition(
                raw_post_id=raw_post.id,
                summary=item.summary,
                condition_text=item.condition_text,
                is_obvious_consensus=item.is_obvious_consensus,
            )
            session.add(db_impl)
            await session.flush()
            implicit_db_list.append((item.target_claim_id, db_impl.id))

        # 4. 创建 Conclusion
        conclusion_id_map: dict[int, int] = {}
        conclusion_db_ids: list[int] = []
        for claim_id, ec in result.get("conclusions", {}).items():
            db_conc = Conclusion(
                raw_post_id=raw_post.id,
                author_id=author_db.id,
                summary=ec.summary,
                claim=ec.claim,
                verifiable_statement=ec.verifiable_statement,
                author_confidence=ec.author_confidence,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_id_map[claim_id] = db_conc.id
            conclusion_db_ids.append(db_conc.id)

        # 5. 创建 Prediction
        prediction_id_map: dict[int, int] = {}
        for claim_id, ep in result.get("predictions", {}).items():
            temporal_validity = "has_timeframe" if ep.temporal_note else "no_timeframe"
            db_pred = Prediction(
                raw_post_id=raw_post.id,
                author_id=author_db.id,
                summary=ep.summary,
                claim=ep.claim,
                temporal_note=ep.temporal_note,
                temporal_validity=temporal_validity,
                author_confidence=ep.author_confidence,
            )
            session.add(db_pred)
            await session.flush()
            prediction_id_map[claim_id] = db_pred.id

        # 6. 创建 Solution
        solution_id_map: dict[int, int] = {}
        for claim_id, es in result.get("solutions", {}).items():
            db_sol = Solution(
                raw_post_id=raw_post.id,
                author_id=author_db.id,
                summary=es.summary,
                claim=es.claim,
                action_type=es.action_type,
                action_target=es.action_target,
                action_rationale=es.action_rationale,
            )
            session.add(db_sol)
            await session.flush()
            solution_id_map[claim_id] = db_sol.id

        # 综合 id maps
        all_id_maps: dict[str, dict[int, int]] = {
            "fact": fact_id_map,
            "assumption": assumption_id_map,
            "conclusion": conclusion_id_map,
            "prediction": prediction_id_map,
            "solution": solution_id_map,
        }

        # 7. 创建 EntityRelationship 边（声明间边）
        premise_conclusion_ids: set[int] = set()
        conclusion_adj: dict[int, list[int]] = defaultdict(list)

        for e in edges:
            src_type = _get_entity_type(e.from_id, class_map)
            tgt_type = _get_entity_type(e.to_id, class_map)
            if src_type is None or tgt_type is None:
                continue

            src_db_id = all_id_maps.get(src_type, {}).get(e.from_id)
            tgt_db_id = all_id_maps.get(tgt_type, {}).get(e.to_id)
            if src_db_id is None or tgt_db_id is None:
                logger.warning(
                    f"[v5] Edge skipped: {src_type}[{e.from_id}] → {tgt_type}[{e.to_id}] (db id missing)"
                )
                continue

            edge_type = _derive_edge_type(src_type, tgt_type)
            db_rel = EntityRelationship(
                raw_post_id=raw_post.id,
                source_type=src_type,
                source_id=src_db_id,
                target_type=tgt_type,
                target_id=tgt_db_id,
                edge_type=edge_type,
            )
            session.add(db_rel)

            if edge_type == "conclusion_supports_conclusion":
                premise_conclusion_ids.add(src_db_id)
                conclusion_adj[src_db_id].append(tgt_db_id)

        # 7b. 创建隐含条件边
        for (target_claim_id, impl_db_id) in implicit_db_list:
            tgt_type = _get_entity_type(target_claim_id, class_map)
            if tgt_type is None:
                continue
            tgt_db_id = all_id_maps.get(tgt_type, {}).get(target_claim_id)
            if tgt_db_id is None:
                continue

            db_rel = EntityRelationship(
                raw_post_id=raw_post.id,
                source_type="implicit_condition",
                source_id=impl_db_id,
                target_type=tgt_type,
                target_id=tgt_db_id,
                edge_type="implicit_conditions_conclusion",
            )
            session.add(db_rel)

        await session.flush()

        # 8. 识别核心结论（is_core_conclusion）
        for db_id in conclusion_db_ids:
            if db_id not in premise_conclusion_ids:
                conc_r = await session.exec(
                    select(Conclusion).where(Conclusion.id == db_id)
                )
                conc = conc_r.first()
                if conc:
                    conc.is_core_conclusion = True
                    session.add(conc)

        await session.flush()

        # 9. 检测结论环（is_in_cycle）
        cycle_ids = _find_cycle_nodes(conclusion_db_ids, conclusion_adj)
        if cycle_ids:
            logger.warning(
                f"[v5] Detected {len(cycle_ids)} conclusion(s) in logic cycle: {cycle_ids}"
            )
            for db_id in cycle_ids:
                conc_r = await session.exec(
                    select(Conclusion).where(Conclusion.id == db_id)
                )
                conc = conc_r.first()
                if conc:
                    conc.is_in_cycle = True
                    session.add(conc)

        # 10. 标记 RawPost 已处理，保存叙事摘要
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
        n_predictions = len(result.get("predictions", {}))
        n_solutions = len(result.get("solutions", {}))
        n_implicit = len(implicit_items)

        logger.info(
            f"[v5] RawPost {raw_post.id} processed: "
            f"{n_facts} facts, {n_assumptions} assumptions, "
            f"{n_implicit} implicit, {n_conclusions} conclusions "
            f"(core={len(conclusion_db_ids) - len(premise_conclusion_ids.intersection(conclusion_db_ids))}), "
            f"{n_predictions} predictions, {n_solutions} solutions, "
            f"{len(edges)} edges"
        )

        # 构造标准 ExtractionResult 用于返回（兼容调用方）
        extraction_result = _to_extraction_result(result, implicit_items, edges, class_map)
        extraction_result.article_summary = article_summary
        return extraction_result

    # ── LLM 调用包装 ─────────────────────────────────────────────────────

    async def _step1_claims(
        self, content: str, platform: str, author: str, today: str,
        author_intent: str | None = None,
    ) -> Step1Result | None:
        from anchor.extract.prompts import v5_step1_claims as p
        user_msg = p.build_user_message(content, platform, author, today, author_intent)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP1_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, Step1Result, "Step1")

    async def _step1_claims_policy(
        self,
        content: str,
        platform: str,
        author: str,
        today: str,
        author_intent: str | None = None,
    ) -> Step1PolicyResult | None:
        from anchor.extract.prompts import v5_step1_policy as p
        user_msg = p.build_user_message(content, platform, author, today, author_intent)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP1_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, Step1PolicyResult, "Step1(policy)")

    async def _step1_policy_themes(self, content: str) -> list[str]:
        """Step A: 快速主旨扫描，返回 theme 名称列表（3-8个）"""
        from anchor.extract.prompts.v5_step1_policy import (
            SYSTEM_THEME_SCAN, build_theme_scan_message,
        )
        user = build_theme_scan_message(content)
        raw = await _call_llm(SYSTEM_THEME_SCAN, user, max_tokens=400)
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

    # ------------------------------------------------------------------
    # 长文档专用方法（content > LONG_DOC_THRESHOLD 时触发）
    # ------------------------------------------------------------------

    LONG_DOC_THRESHOLD = 15000

    async def _extract_paragraphs_for_theme(self, content: str, theme: str) -> str:
        """从全文中提取与 theme 相关的段落（原文照录）。"""
        from anchor.extract.prompts.v5_step1_policy import (
            SYSTEM_PARA_EXTRACT, build_para_extract_message,
        )
        result = await _call_llm(
            SYSTEM_PARA_EXTRACT, build_para_extract_message(content, theme), max_tokens=2000
        )
        return result.strip() if result else "（无相关内容）"

    async def _step1_policy_single_theme(
        self,
        theme: str,
        curr_paragraphs: str,
        prior_paragraphs: str | None,
        web_snippet: str | None,
    ) -> "PolicySchema | None":
        """对单个主旨做完整六维提取。"""
        from anchor.extract.prompts.v5_step1_policy import (
            SYSTEM_SINGLE_THEME, build_single_theme_message,
        )
        from anchor.extract.schemas import PolicySchema
        raw = await _call_llm(
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
        self,
        content: str,
        prior_content: str | None,
        themes: list[str],
        web_ctx: dict[str, str],
    ) -> "PolicyExtractionResult":
        """长文档专用：逐主旨并行提取。
        Step A2: 每主旨并行提取相关段落（当年 + 上年）
        Step B:  每主旨单独六维提取
        最后:    单独一次 facts + conclusions 提取
        """
        import asyncio
        from anchor.extract.schemas import PolicyExtractionResult, PolicySchema, RawClaim
        from anchor.extract.prompts.v5_step1_policy import (
            SYSTEM_FACTS_CONCLUSIONS, build_facts_conclusions_message,
        )

        sem_para = asyncio.Semaphore(5)
        sem_b = asyncio.Semaphore(3)

        async def get_paragraphs(theme: str) -> tuple[str, str, str | None]:
            async with sem_para:
                curr_p = await self._extract_paragraphs_for_theme(content, theme)
                prior_p = (
                    await self._extract_paragraphs_for_theme(prior_content, theme)
                    if prior_content else None
                )
            return theme, curr_p, prior_p

        async def run_step_b(theme: str, curr_p: str, prior_p: str | None) -> "PolicySchema | None":
            async with sem_b:
                return await self._step1_policy_single_theme(
                    theme, curr_p, prior_p, web_ctx.get(theme)
                )

        # Step A2
        logger.info(f"[v5/policy/long] Step A2: paragraph extraction for {len(themes)} themes")
        para_results = await asyncio.gather(
            *[get_paragraphs(t) for t in themes], return_exceptions=True
        )

        # Step B
        logger.info(f"[v5/policy/long] Step B: per-theme extraction")
        valid_paras = [r for r in para_results if isinstance(r, tuple)]
        policy_results = await asyncio.gather(
            *[run_step_b(theme, curr_p, prior_p) for theme, curr_p, prior_p in valid_paras],
            return_exceptions=True,
        )
        policies = [p for p in policy_results if isinstance(p, PolicySchema)]
        logger.info(f"[v5/policy/long] Step B done: {len(policies)}/{len(themes)} policies")

        # Facts + Conclusions
        facts: list[RawClaim] = []
        conclusions: list[RawClaim] = []
        try:
            fc_raw = await _call_llm(
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
            is_relevant_content=True,
            policies=policies,
            facts=facts,
            conclusions=conclusions,
        )

    async def _step1_policy_full(
        self,
        current_content: str,
        prior_content: str | None,
        web_ctx: dict[str, str],
        themes: list[str] | None = None,
    ) -> PolicyExtractionResult | None:
        """Step B: 完整政策提取（当年全文 + 上年全文 + 联网搜索背景）"""
        from anchor.extract.prompts.v5_step1_policy import (
            SYSTEM_FULL_EXTRACT, build_full_extract_message,
        )
        user = build_full_extract_message(current_content, prior_content, web_ctx, themes=themes)
        raw = await _call_llm(SYSTEM_FULL_EXTRACT, user, max_tokens=8000)
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

    async def _extract_policy(
        self,
        raw_post: RawPost,
        session: AsyncSession,
        content: str,
        platform: str,
        author: str,
        today: str,
        author_intent: str | None,
    ) -> ExtractionResult | None:
        """Policy 模式完整提取流程（v3）：
        Step A 主旨扫描 → 并行 Tavily 搜索 + 获取上年文档 → Step B 完整提取 → Step5 摘要 → DB写入
        """
        import asyncio
        from anchor.verify.web_searcher import web_search

        # Step A: 主旨扫描
        themes = await self._step1_policy_themes(content)
        logger.info(f"[v5/policy] Step A themes: {themes}")

        # 并行：Tavily 搜索（每主旨一次） + 获取上年文档
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

        # 并行搜索 + 获取上年 post
        web_ctx: dict[str, str] = {}
        if themes:
            search_coros = [search_theme(t) for t in themes]
            prior_post_coro = self._find_prior_policy_post(prior_year, session)
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
            prior_post = await self._find_prior_policy_post(prior_year, session)

        prior_content: str | None = None
        if prior_post and isinstance(prior_post, RawPost):
            prior_content = prior_post.enriched_content or prior_post.content
            logger.info(f"[v5/policy] Found prior year post id={prior_post.id}")
        else:
            logger.info(f"[v5/policy] No prior year post in DB, auto-fetching {prior_year} document")
            prior_content = await self._fetch_prior_year_content(raw_post, prior_year)

        # Step B: 根据文档长度选择提取策略
        if len(content) > self.LONG_DOC_THRESHOLD:
            logger.info(
                f"[v5/policy] Long doc detected ({len(content)} chars > {self.LONG_DOC_THRESHOLD}), "
                f"switching to per-theme extraction"
            )
            result = await self._extract_policy_long(content, prior_content, themes, web_ctx)
        else:
            result = await self._step1_policy_full(content, prior_content, web_ctx, themes=themes)
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
        step5 = await self._step5_summary(core_conclusions_text, [], key_facts_text)
        if step5:
            article_summary = step5
            logger.info(f"[v5/policy] Step5 summary: {article_summary!r}")

        # DB 写入（v3 新实体）
        await self._write_policy_v3_entities(result, raw_post, session, article_summary)

        # 构造返回值
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

    async def _write_policy_entities(
        self,
        result: Step1PolicyResult,
        raw_post: RawPost,
        session: AsyncSession,
        article_summary: str | None = None,
    ) -> None:
        """将 Step1PolicyResult 写入数据库（policy_themes / policy_items / facts / conclusions）"""
        # 1. 写 PolicyTheme + PolicyItem
        # issuing_authority / authority_level 由 Chain 2 写入，此处不覆盖
        policy_db_ids: list[int] = []
        for theme_item in result.themes:
            db_theme = PolicyTheme(
                raw_post_id=raw_post.id,
                theme_name=theme_item.theme_name,
                background=theme_item.background,   # Chain 1 从文件推断
                enforcement_note=theme_item.enforcement_note,
                has_enforcement_teeth=theme_item.has_enforcement_teeth,
            )
            session.add(db_theme)
            await session.flush()  # 获取 theme.id

            for policy_item in theme_item.policies:
                db_policy = PolicyItem(
                    raw_post_id=raw_post.id,
                    policy_theme_id=db_theme.id,
                    summary=policy_item.summary,
                    policy_text=policy_item.policy_text,
                    urgency=policy_item.urgency,
                    change_type=None,   # 提取时不填，由 compare_policies 填写
                    metric_value=policy_item.metric_value,
                    target_year=policy_item.target_year,
                    is_hard_target=policy_item.is_hard_target,
                )
                session.add(db_policy)
                await session.flush()
                policy_db_ids.append(db_policy.id)

        # 3. 写 Fact（变化标注事实）
        fact_db_ids: list[int] = []
        for f in result.facts:
            db_fact = Fact(
                raw_post_id=raw_post.id,
                summary=f.summary,
                claim=f.text,
                verifiable_statement=f.text,
            )
            session.add(db_fact)
            await session.flush()
            fact_db_ids.append(db_fact.id)

        # 4. 写 Conclusion（总体政策方向结论）
        conclusion_db_ids: list[int] = []
        for c in result.conclusions:
            db_conc = Conclusion(
                raw_post_id=raw_post.id,
                summary=c.summary,
                claim=c.text,
                verifiable_statement=c.text,
                is_core_conclusion=True,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_db_ids.append(db_conc.id)

        # 5. 写 EntityRelationship 边
        #    fact → conclusion（fact_supports_conclusion）
        for fact_id in fact_db_ids:
            for conc_id in conclusion_db_ids:
                db_rel = EntityRelationship(
                    raw_post_id=raw_post.id,
                    source_type="fact",
                    source_id=fact_id,
                    target_type="conclusion",
                    target_id=conc_id,
                    edge_type="fact_supports_conclusion",
                )
                session.add(db_rel)

        #    policy_item → conclusion（policy_supports_conclusion）
        for pol_id in policy_db_ids:
            for conc_id in conclusion_db_ids:
                db_rel = EntityRelationship(
                    raw_post_id=raw_post.id,
                    source_type="policy_item",
                    source_id=pol_id,
                    target_type="conclusion",
                    target_id=conc_id,
                    edge_type="policy_supports_conclusion",
                )
                session.add(db_rel)

        # 6. 标记 RawPost 已处理
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        if article_summary:
            raw_post.content_summary = article_summary
        session.add(raw_post)
        await session.flush()
        await session.commit()

        logger.info(
            f"[v5/policy] RawPost {raw_post.id} written: "
            f"{len(result.themes)} themes, {len(policy_db_ids)} policies, "
            f"{len(fact_db_ids)} facts, {len(conclusion_db_ids)} conclusions"
        )

    async def _write_policy_v3_entities(
        self,
        result: PolicyExtractionResult,
        raw_post: RawPost,
        session: AsyncSession,
        article_summary: str | None = None,
    ) -> None:
        """将 PolicyExtractionResult 写入数据库（policies / policy_measures / facts / conclusions）"""
        conclusion_db_ids: list[int] = []
        fact_db_ids: list[int] = []

        # 1. 写 Policy + PolicyMeasure
        for p_schema in result.policies:
            db_policy = Policy(
                raw_post_id=raw_post.id,
                theme=p_schema.theme,
                change_summary=p_schema.change_summary,
                target=p_schema.target,
                target_prev=p_schema.target_prev,
                intensity=p_schema.intensity,
                intensity_prev=p_schema.intensity_prev,
                intensity_note=p_schema.intensity_note,
                intensity_note_prev=p_schema.intensity_note_prev,
                background=p_schema.background,
                background_prev=p_schema.background_prev,
                organization=p_schema.organization,
                organization_prev=p_schema.organization_prev,
            )
            session.add(db_policy)
            await session.flush()  # 获取 policy.id

            for m_schema in p_schema.measures:
                db_measure = PolicyMeasure(
                    policy_id=db_policy.id,
                    raw_post_id=raw_post.id,
                    summary=m_schema.summary,
                    measure_text=m_schema.measure_text,
                    trend=m_schema.trend,
                    trend_note=m_schema.trend_note,
                )
                session.add(db_measure)

        # 2. 写 Fact（变化标注事实）
        for f in result.facts:
            db_fact = Fact(
                raw_post_id=raw_post.id,
                summary=f.summary,
                claim=f.text,
                verifiable_statement=f.text,
            )
            session.add(db_fact)
            await session.flush()
            fact_db_ids.append(db_fact.id)

        # 3. 写 Conclusion（总体政策方向结论）
        for c in result.conclusions:
            db_conc = Conclusion(
                raw_post_id=raw_post.id,
                summary=c.summary,
                claim=c.text,
                verifiable_statement=c.text,
                is_core_conclusion=True,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_db_ids.append(db_conc.id)

        # 4. 写 EntityRelationship 边（fact → conclusion）
        for fact_id in fact_db_ids:
            for conc_id in conclusion_db_ids:
                db_rel = EntityRelationship(
                    raw_post_id=raw_post.id,
                    source_type="fact",
                    source_id=fact_id,
                    target_type="conclusion",
                    target_id=conc_id,
                    edge_type="fact_supports_conclusion",
                )
                session.add(db_rel)

        # 5. 标记 RawPost 已处理
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

    async def _step2_merge(self, claims: List[RawClaim]) -> Step2Result | None:
        from anchor.extract.prompts import v5_step2_merge as p
        user_msg = p.build_user_message(claims)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP2_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, Step2Result, "Step2")

    async def _step3_classify(
        self,
        claims: List[RawClaim],
        edges: List[RawEdge],
        core_ids: Set[int],
        isolated_ids: Set[int],
    ) -> Step3Result | None:
        from anchor.extract.prompts import v5_step3_classify as p
        user_msg = p.build_user_message(claims, edges, core_ids, isolated_ids)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP3_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, Step3Result, "Step3")

    async def _step4_implicit(
        self, inferences: List[Tuple[str, str, int]]
    ) -> Step4Result | None:
        from anchor.extract.prompts import v5_step4_implicit as p
        user_msg = p.build_user_message(inferences)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP4_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, Step4Result, "Step4")

    async def _step5_summary(
        self,
        core_conclusions: List[str],
        sub_conclusions: List[str],
        key_facts: List[str],
    ) -> str | None:
        from anchor.extract.prompts import v5_step5_summary as p
        user_msg = p.build_user_message(core_conclusions, sub_conclusions, key_facts)
        raw = await _call_llm(p.SYSTEM, user_msg, _STEP5_TOKENS)
        if raw is None:
            return None
        parsed = _parse_json(raw, _SummaryResult, "Step5")
        return parsed.summary if parsed else None

    async def fetch_prior_year_and_compare(
        self,
        current_post_id: int,
        session: AsyncSession,
        search_query: str | None = None,
    ) -> "PolicyComparisonResult | None":
        """自动搜索上年同类政策文档，提取后与当年比对。

        流程：
          1. 读当年 post，推断年份
          2. 检查 DB 是否已有上年 policy 文档
          3. 若无：搜索 + Jina 抓全文 + 写 RawPost + policy 提取
          4. compare_policies(current_post_id, prior_post_id)

        Args:
            current_post_id: 当年政策文档的 RawPost.id
            session:         AsyncSession
            search_query:    自定义搜索词（留 None 则自动推断）
        """
        import hashlib
        from datetime import datetime as _dt

        # ── 1. 读当年 post ──────────────────────────────────────────────
        current_post_r = await session.exec(select(RawPost).where(RawPost.id == current_post_id))
        current_post = current_post_r.first()
        if current_post is None:
            logger.error(f"[fetch_prior] post {current_post_id} not found")
            return None

        current_year = current_post.posted_at.year if current_post.posted_at else _dt.utcnow().year
        prior_year = current_year - 1

        # ── 2. 检查 DB 是否已有上年 policy post ─────────────────────────
        existing_prior = await self._find_prior_policy_post(prior_year, session)
        if existing_prior:
            logger.info(f"[fetch_prior] Found existing prior year post id={existing_prior.id} ({prior_year})")
            return await self.compare_policies(current_post_id, existing_prior.id, session)

        # ── 3. 联网搜索上年文档 URL ──────────────────────────────────────
        from anchor.verify.web_searcher import web_search
        query = search_query or f"{prior_year}年政府工作报告 全文"
        logger.info(f"[fetch_prior] Searching: {query!r}")
        results = await web_search(
            query,
            max_results=5,
            include_domains=["gov.cn", "xinhuanet.com", "npc.gov.cn", "people.com.cn"],
        )
        if not results:
            logger.warning("[fetch_prior] No search results found")
            return None

        best_url = results[0].url
        logger.info(f"[fetch_prior] Top result: {best_url}")

        # ── 4. Jina 抓取全文 ─────────────────────────────────────────────
        from anchor.collect.web import WebCollector
        collector = WebCollector()
        post_data = await collector.collect_by_url(best_url)
        if post_data is None or not post_data.content or len(post_data.content) < 500:
            logger.warning(f"[fetch_prior] Jina fetch failed or too short for {best_url}")
            # 降级：尝试第二个结果
            for r in results[1:]:
                post_data = await collector.collect_by_url(r.url)
                if post_data and len(post_data.content) >= 500:
                    best_url = r.url
                    break
            if post_data is None or len(post_data.content) < 500:
                logger.error("[fetch_prior] All URLs failed, aborting")
                return None

        # ── 5. 写入 RawPost（去重：同 external_id 则复用）────────────────
        external_id = hashlib.md5(best_url.encode()).hexdigest()[:16]
        existing_rp_r = await session.exec(
            select(RawPost).where(
                RawPost.source == "web",
                RawPost.external_id == external_id,
            )
        )
        prior_post = existing_rp_r.first()
        if prior_post is None:
            prior_post = RawPost(
                source="web",
                external_id=external_id,
                content=post_data.content,
                author_name=post_data.author_name or f"国务院/{prior_year}",
                url=best_url,
                # 政府工作报告固定在 3 月初发布
                posted_at=_dt(prior_year, 3, 5),
                is_processed=False,
            )
            session.add(prior_post)
            await session.flush()
            logger.info(f"[fetch_prior] Created RawPost id={prior_post.id} for {prior_year} ({len(post_data.content)} chars)")
        else:
            logger.info(f"[fetch_prior] Reusing existing RawPost id={prior_post.id}")

        # ── 6. Policy 模式提取 ───────────────────────────────────────────
        if not prior_post.is_processed:
            result = await self.extract(prior_post, session, content_mode="policy")
            if result is None or not result.is_relevant_content:
                logger.warning(f"[fetch_prior] Extraction failed or not relevant for prior year post")
                return None
            logger.info(f"[fetch_prior] {prior_year} report extracted: {len(result.facts)} facts, {len(result.conclusions)} conclusions")
        else:
            logger.info(f"[fetch_prior] Prior year post already extracted")

        # ── 7. 比对 ─────────────────────────────────────────────────────
        return await self.compare_policies(current_post_id, prior_post.id, session)

    async def _fetch_prior_year_content(self, current_post: RawPost, prior_year: int) -> str | None:
        """联网搜索并抓取上年同类政策文档全文（仅用于对比，不写 DB）。"""
        from anchor.verify.web_searcher import web_search
        from anchor.collect.web import WebCollector

        # 从当年文档标题推断搜索词
        topic = current_post.content_topic or ""
        if "政府工作报告" in (current_post.content or "") or "政府工作报告" in topic:
            query = f"{prior_year}年政府工作报告 全文"
        else:
            query = f"{prior_year}年 {topic or '政策文件'} 全文"

        logger.info(f"[v5/policy] Searching prior year doc: {query!r}")
        try:
            results = await web_search(
                query,
                max_results=5,
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

    async def _find_prior_policy_post(self, prior_year: int, session: AsyncSession) -> "RawPost | None":
        """在 DB 中查找已提取过 policy 的上年文档（先查 v3 Policy 表，再查 v2 PolicyTheme 表）。"""
        from datetime import datetime as _dt
        year_start = _dt(prior_year, 1, 1)
        year_end = _dt(prior_year, 12, 31)
        # v3: Policy 表
        r = await session.exec(
            select(RawPost)
            .join(Policy, Policy.raw_post_id == RawPost.id)
            .where(RawPost.posted_at >= year_start, RawPost.posted_at <= year_end)
            .limit(1)
        )
        post = r.first()
        if post:
            return post
        # v2 fallback: PolicyTheme 表
        r = await session.exec(
            select(RawPost)
            .join(PolicyTheme, PolicyTheme.raw_post_id == RawPost.id)
            .where(RawPost.posted_at >= year_start, RawPost.posted_at <= year_end)
            .limit(1)
        )
        return r.first()

    async def _compare_policy_llm(
        self,
        current_year: str,
        current_policies: list[dict],
        prior_year: str,
        prior_policies: list[dict],
    ) -> PolicyComparisonResult | None:
        from anchor.extract.prompts import v5_compare_policy as p
        user_msg = p.build_user_message(current_year, current_policies, prior_year, prior_policies)
        raw = await _call_llm(p.SYSTEM, user_msg, _MAX_TOKENS)
        if raw is None:
            return None
        return _parse_json(raw, PolicyComparisonResult, "ComparePolicy")

    async def compare_policies(
        self,
        current_post_id: int,
        prior_post_id: int,
        session: AsyncSession,
    ) -> PolicyComparisonResult | None:
        """对比两篇政策文档，标注 change_type 并写入当年 PolicyItem，删除摘要写入 Fact。

        Args:
            current_post_id: 当年政策文档的 RawPost id
            prior_post_id:   上年政策文档的 RawPost id
            session:         异步数据库 Session
        """
        # 读取当年 RawPost（用于获取年份信息）
        current_post_r = await session.exec(select(RawPost).where(RawPost.id == current_post_id))
        current_post = current_post_r.first()
        prior_post_r = await session.exec(select(RawPost).where(RawPost.id == prior_post_id))
        prior_post = prior_post_r.first()

        if current_post is None or prior_post is None:
            logger.error(f"[compare_policies] Post not found: current={current_post_id}, prior={prior_post_id}")
            return None

        current_year = str(current_post.posted_at.year) if current_post.posted_at else "当年"
        prior_year = str(prior_post.posted_at.year) if prior_post.posted_at else "上年"

        # 读取当年 PolicyTheme + PolicyItem
        current_themes_r = await session.exec(
            select(PolicyTheme).where(PolicyTheme.raw_post_id == current_post_id)
        )
        current_themes = current_themes_r.all()
        theme_name_map: dict[int, str] = {t.id: t.theme_name for t in current_themes}

        current_items_r = await session.exec(
            select(PolicyItem).where(PolicyItem.raw_post_id == current_post_id)
        )
        current_items = current_items_r.all()

        # 读取上年 PolicyTheme + PolicyItem
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

        # 幂等检查：若已有标注则跳过
        already_annotated = any(item.change_type is not None for item in current_items)
        if already_annotated:
            logger.info(f"[compare_policies] post {current_post_id} already annotated, skipping")
            return None

        # 构建传给 LLM 的列表
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

        comparison = await self._compare_policy_llm(
            current_year, current_policies_list, prior_year, prior_policies_list
        )
        if comparison is None:
            logger.warning("[compare_policies] LLM comparison failed")
            return None

        # 批量更新当年 PolicyItem.change_type / change_note
        item_map: dict[int, PolicyItem] = {item.id: item for item in current_items}
        for ann in comparison.annotations:
            item = item_map.get(ann.policy_id)
            if item is None:
                logger.warning(f"[compare_policies] policy_id {ann.policy_id} not found, skipping")
                continue
            item.change_type = ann.change_type
            item.change_note = ann.change_note
            session.add(item)

        # 将 deleted_summaries 写入当年 post 的 Fact 表
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


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _call_llm(system: str, user: str, max_tokens: int) -> str | None:
    resp = await chat_completion(system=system, user=user, max_tokens=max_tokens)
    if resp is None:
        return None
    logger.debug(f"[v5] LLM: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}")
    return resp.content


def _parse_json(raw: str, model_cls, step_name: str):
    """从 LLM 返回文本中提取 JSON 并解析为给定 Pydantic 模型。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()

    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning(f"[v5] {step_name}: no JSON found in output")
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        return model_cls.model_validate(data)
    except Exception as exc:
        logger.warning(f"[v5] {step_name} parse error: {exc}\nRaw: {raw[:400]}")
        return None


def _apply_merges(
    claims: List[RawClaim],
    edges: List[RawEdge],
    merges: List[MergeGroup],
) -> tuple[List[RawClaim], List[RawEdge]]:
    """应用合并组：将 discard 节点的所有边重定向到 keep 节点，去重，更新 summary。"""
    # 构建 discard → keep 映射
    remap: dict[int, int] = {}
    summary_update: dict[int, str] = {}
    for mg in merges:
        for d in mg.discard:
            remap[d] = mg.keep
        summary_update[mg.keep] = mg.merged_summary

    # 过滤掉 discard 节点，更新 keep 节点的 text + summary
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

    # 重定向边，去重，去自环
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


def _get_entity_type(claim_id: int, class_map: dict[int, ClassifiedEntity]) -> str | None:
    ce = class_map.get(claim_id)
    if ce is None:
        return None
    return ce.entity_type


def _derive_edge_type(src_type: str, tgt_type: str) -> str:
    """根据源/目标实体类型推导边类型。"""
    if src_type == "implicit_condition":
        return "implicit_conditions_conclusion"
    if src_type == "assumption":
        return "assumption_conditions_conclusion"
    if src_type == "fact":
        return "fact_supports_conclusion"
    if src_type == "conclusion":
        if tgt_type == "conclusion":
            return "conclusion_supports_conclusion"
        if tgt_type == "prediction":
            return "conclusion_leads_to_prediction"
        if tgt_type == "solution":
            return "conclusion_enables_solution"
    if src_type == "prediction" and tgt_type == "solution":
        return "conclusion_enables_solution"
    # fallback
    return "fact_supports_conclusion"


def _build_extraction_result(
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

    for c in claims:
        ce = class_map.get(c.id)
        if ce is None:
            # 未被分类的声明默认为 fact
            facts[c.id] = ExtractedFact(
                summary=c.summary,
                claim=c.text,
                verifiable_statement=c.text,
            )
            continue

        et = ce.entity_type
        if et == "fact":
            facts[c.id] = ExtractedFact(
                summary=c.summary,
                claim=c.text,
                verifiable_statement=ce.verifiable_statement or c.text,
            )
        elif et == "assumption":
            assumptions[c.id] = ExtractedAssumption(
                summary=c.summary,
                condition_text=c.text,
                temporal_note=ce.temporal_note,
            )
        elif et == "conclusion":
            conclusions[c.id] = ExtractedConclusion(
                summary=c.summary,
                claim=c.text,
                verifiable_statement=ce.verifiable_statement or c.text,
                author_confidence=ce.author_confidence,
            )
        elif et == "prediction":
            predictions[c.id] = ExtractedPrediction(
                summary=c.summary,
                claim=c.text,
                temporal_note=ce.temporal_note,
                author_confidence=ce.author_confidence,
            )
        elif et == "solution":
            solutions[c.id] = ExtractedSolution(
                summary=c.summary,
                claim=c.text,
                action_type=ce.action_type,
                action_target=ce.action_target,
                action_rationale=ce.action_rationale,
            )
        else:
            # unknown type → default to fact
            facts[c.id] = ExtractedFact(
                summary=c.summary,
                claim=c.text,
                verifiable_statement=c.text,
            )

    return {
        "facts": facts,
        "assumptions": assumptions,
        "conclusions": conclusions,
        "predictions": predictions,
        "solutions": solutions,
    }


def _to_extraction_result(
    result: dict,
    implicit_items: List[ImplicitConditionItem],
    edges: List[RawEdge],
    class_map: dict[int, ClassifiedEntity],
) -> ExtractionResult:
    """将内部 dict 结构转换为标准 ExtractionResult（供调用方消费）。"""
    facts = list(result.get("facts", {}).values())
    assumptions = list(result.get("assumptions", {}).values())
    conclusions = list(result.get("conclusions", {}).values())
    predictions = list(result.get("predictions", {}).values())
    solutions = list(result.get("solutions", {}).values())

    implicit_conditions = [
        ExtractedImplicitCondition(
            summary=item.summary,
            condition_text=item.condition_text,
            is_obvious_consensus=item.is_obvious_consensus,
        )
        for item in implicit_items
    ]

    # 构建简化关系列表（使用实体列表顺序下标，供外部展示）
    # 注意：实际 DB 写库使用的是 claim_id → db_id 映射，不依赖此处的 index
    fact_ids = list(result.get("facts", {}).keys())
    assumption_ids = list(result.get("assumptions", {}).keys())
    conclusion_ids = list(result.get("conclusions", {}).keys())
    prediction_ids = list(result.get("predictions", {}).keys())
    solution_ids = list(result.get("solutions", {}).keys())

    def get_type_and_index(claim_id: int) -> tuple[str, int] | None:
        ce = class_map.get(claim_id)
        et = ce.entity_type if ce else "fact"
        for id_list, type_name in [
            (fact_ids, "fact"),
            (assumption_ids, "assumption"),
            (conclusion_ids, "conclusion"),
            (prediction_ids, "prediction"),
            (solution_ids, "solution"),
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
                    source_type=src[0],
                    source_index=src[1],
                    target_type=tgt[0],
                    target_index=tgt[1],
                    edge_type=_derive_edge_type(src[0], tgt[0]),
                )
            )

    return ExtractionResult(
        is_relevant_content=True,
        facts=facts,
        assumptions=assumptions,
        implicit_conditions=implicit_conditions,
        conclusions=conclusions,
        predictions=predictions,
        solutions=solutions,
        relationships=relationships,
    )


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
