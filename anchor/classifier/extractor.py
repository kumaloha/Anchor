"""
Layer2 — 观点提取器（v2 — 六实体）
====================================
输入：一条原始帖子（RawPost）
输出：写入数据库的 Fact / Assumption / ImplicitCondition / Conclusion / Prediction / Solution / Logic 记录

写库顺序（Logic 依赖前序实体的 DB ID）：
  1. 批量创建 Fact → fact_id_map
  2. 创建 Assumption → assumption_id_map
  3. 创建 ImplicitCondition → ic_id_map
  4. 创建 Conclusion → conclusion_id_map
  5. 创建 Prediction → prediction_id_map
  6. 创建 Solution → solution_id_map
  7. 创建 Logic（含 chain_summary + chain_type）
  8. 更新 RawPost.is_processed
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.classifier.prompts import PROMPT_REGISTRY, DEFAULT_PROMPT_VERSION
from anchor.classifier.schemas import (
    ExtractionResult,
    ExtractedFact,
    ExtractedAssumption,
    ExtractedImplicitCondition,
    ExtractedPrediction,
)
from anchor.llm_client import chat_completion
from anchor.models import (
    Assumption,
    Author,
    Conclusion,
    ConclusionStatus,
    Fact,
    FactStatus,
    ImplicitCondition,
    Logic,
    Prediction,
    PredictionStatus,
    RawPost,
    Solution,
    SolutionStatus,
    Topic,
    VerificationReference,
    _utcnow,
)

_MAX_TOKENS = 8000


class Extractor:
    """観点提取器（v2 — 六实体）

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
        """对一条帖子执行观点提取，写入数据库，返回提取结果。"""
        if raw_post.is_processed:
            logger.debug(f"RawPost {raw_post.id} already processed, skipping")
            return None

        if raw_post.is_duplicate:
            logger.info(
                f"RawPost {raw_post.id} is a cross-platform duplicate "
                f"(original_post_id={raw_post.original_post_id}), skipping extraction"
            )
            return None

        content = raw_post.enriched_content or raw_post.content

        # 追加图片描述（若帖子含图片且视觉模型可用）
        if raw_post.media_json:
            from anchor.collector.media_describer import describe_media
            media_desc = await describe_media(raw_post)
            if media_desc:
                content = content + "\n\n--- 图片内容 ---\n" + media_desc

        user_msg = self._prompt.build_user_message(
            content=content,
            platform=raw_post.source,
            author=raw_post.author_name,
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
        raw_extraction_str = raw_json

        # ── Step 1：创建所有 Fact ─────────────────────────────────────────
        fact_id_map: dict[int, int] = {}
        for idx, ef in enumerate(result.facts):
            db_fact = await _save_fact(session, ef, raw_post_id=raw_post.id)
            fact_id_map[idx] = db_fact.id

        # ── Step 2：创建 Assumption ──────────────────────────────────────
        assumption_id_map: dict[int, int] = {}
        for idx, ea in enumerate(result.assumptions):
            db_assump = await _save_assumption(session, ea, raw_post_id=raw_post.id)
            assumption_id_map[idx] = db_assump.id

        # ── Step 3：创建 ImplicitCondition ──────────────────────────────
        ic_id_map: dict[int, int] = {}
        for idx, eic in enumerate(result.implicit_conditions):
            db_ic = await _save_implicit_condition(session, eic)
            ic_id_map[idx] = db_ic.id

        # ── Step 4：创建 Conclusion ──────────────────────────────────────
        conclusion_id_map: dict[int, int] = {}
        for idx, ec in enumerate(result.conclusions):
            topic = await _get_or_create_topic(session, ec.topic)
            db_conc = Conclusion(
                topic_id=topic.id,
                author_id=author.id,
                claim=ec.claim,
                canonical_claim=ec.canonical_claim,
                verifiable_statement=ec.verifiable_statement,
                temporal_type="retrospective",
                temporal_note=ec.temporal_note,
                conclusion_type="retrospective",
                time_horizon_note=ec.temporal_note,
                author_confidence=ec.author_confidence,
                author_confidence_note=ec.author_confidence_note,
                source_url=raw_post.url,
                source_platform=raw_post.source,
                posted_at=raw_post.posted_at,
                raw_extraction=raw_extraction_str,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_id_map[idx] = db_conc.id

        # ── Step 5：创建 Prediction ──────────────────────────────────────
        prediction_id_map: dict[int, int] = {}
        for idx, ep in enumerate(result.predictions):
            topic = await _get_or_create_topic(session, ep.topic)
            db_pred = Prediction(
                topic_id=topic.id,
                author_id=author.id,
                claim=ep.claim,
                canonical_claim=ep.canonical_claim,
                verifiable_statement=ep.verifiable_statement,
                temporal_type="predictive",
                temporal_note=ep.temporal_note,
                author_confidence=ep.author_confidence,
                author_confidence_note=ep.author_confidence_note,
                source_url=raw_post.url,
                source_platform=raw_post.source,
                posted_at=raw_post.posted_at,
                raw_extraction=raw_extraction_str,
            )
            session.add(db_pred)
            await session.flush()
            prediction_id_map[idx] = db_pred.id

        # ── Step 6：创建 Solution ───────────────────────────────────────
        solution_id_map: dict[int, int] = {}
        for idx, es in enumerate(result.solutions):
            topic = await _get_or_create_topic(session, es.topic)
            db_sol = Solution(
                topic_id=topic.id,
                author_id=author.id,
                claim=es.claim,
                action_type=es.action_type,
                action_target=es.action_target,
                action_rationale=es.action_rationale,
                source_url=raw_post.url,
                source_platform=raw_post.source,
                posted_at=raw_post.posted_at,
                raw_extraction=raw_extraction_str,
            )
            session.add(db_sol)
            await session.flush()
            solution_id_map[idx] = db_sol.id

        # ── Step 7：创建 Logic（含 chain_summary）───────────────────────
        for el in result.logics:
            if el.logic_type in ("inference", "prediction"):
                conc_id = None
                pred_id = None
                target_label = ""

                if el.logic_type == "inference":
                    conc_id = (
                        conclusion_id_map.get(el.target_index)
                        if el.target_index is not None else None
                    )
                    if conc_id is None:
                        logger.warning(
                            f"Logic inference target_index={el.target_index} not found, skipping"
                        )
                        continue
                    # Use verifiable_statement as label if available
                    ec = result.conclusions[el.target_index] if el.target_index is not None and el.target_index < len(result.conclusions) else None
                    target_label = ec.verifiable_statement if ec else str(el.target_index)
                    chain_type = "inference"

                else:  # prediction
                    pred_id = (
                        prediction_id_map.get(el.target_index)
                        if el.target_index is not None else None
                    )
                    if pred_id is None:
                        logger.warning(
                            f"Logic prediction target_index={el.target_index} not found, skipping"
                        )
                        continue
                    ep = result.predictions[el.target_index] if el.target_index is not None and el.target_index < len(result.predictions) else None
                    target_label = ep.verifiable_statement if ep else str(el.target_index)
                    chain_type = "prediction"

                supporting_ids = [
                    fact_id_map[i] for i in el.supporting_fact_indices if i in fact_id_map
                ]
                assumption_ids_fact = [
                    fact_id_map[i] for i in el.assumption_fact_indices if i in fact_id_map
                ]
                supporting_conc_ids = [
                    conclusion_id_map[i] for i in el.supporting_conclusion_indices
                    if i in conclusion_id_map
                ]
                assump_ids = [
                    assumption_id_map[i] for i in el.assumption_indices
                    if i in assumption_id_map
                ]
                ic_ids = [
                    ic_id_map[i] for i in el.implicit_condition_indices
                    if i in ic_id_map
                ]

                # Build premise labels for chain_summary
                premise_labels: list[str] = []
                for fid_idx in el.supporting_fact_indices:
                    if fid_idx < len(result.facts):
                        ef = result.facts[fid_idx]
                        premise_labels.append(ef.verifiable_statement)
                for aid_idx in el.assumption_indices:
                    if aid_idx < len(result.assumptions):
                        ea = result.assumptions[aid_idx]
                        premise_labels.append(ea.condition_text)
                for cid_idx in el.supporting_conclusion_indices:
                    if cid_idx < len(result.conclusions):
                        ec2 = result.conclusions[cid_idx]
                        premise_labels.append(ec2.verifiable_statement)

                chain_summary = _build_chain_summary(chain_type, premise_labels, target_label)

                db_logic = Logic(
                    logic_type=el.logic_type,
                    conclusion_id=conc_id,
                    prediction_id=pred_id,
                    supporting_fact_ids=json.dumps(supporting_ids),
                    assumption_fact_ids=json.dumps(assumption_ids_fact),
                    supporting_conclusion_ids=json.dumps(supporting_conc_ids),
                    assumption_ids=json.dumps(assump_ids),
                    layer2_implicit_condition_ids=json.dumps(ic_ids),
                    chain_summary=chain_summary,
                    chain_type=chain_type,
                )
                session.add(db_logic)

            elif el.logic_type == "derivation":
                sol_id = (
                    solution_id_map.get(el.solution_index)
                    if el.solution_index is not None else None
                )
                if sol_id is None:
                    logger.warning(
                        f"Logic derivation solution_index={el.solution_index} not found, skipping"
                    )
                    continue

                source_conc_ids = [
                    conclusion_id_map[i] for i in el.source_conclusion_indices
                    if i in conclusion_id_map
                ]
                source_pred_ids = [
                    prediction_id_map[i] for i in el.source_prediction_indices
                    if i in prediction_id_map
                ]

                # Build premise labels
                premise_labels = []
                for cid_idx in el.source_conclusion_indices:
                    if cid_idx < len(result.conclusions):
                        ec2 = result.conclusions[cid_idx]
                        premise_labels.append(ec2.verifiable_statement)
                for pid_idx in el.source_prediction_indices:
                    if pid_idx < len(result.predictions):
                        ep2 = result.predictions[pid_idx]
                        premise_labels.append(ep2.verifiable_statement)

                sol_es = result.solutions[el.solution_index] if el.solution_index is not None and el.solution_index < len(result.solutions) else None
                target_label = sol_es.claim if sol_es else str(el.solution_index)
                chain_summary = _build_chain_summary("recommendation", premise_labels, target_label)

                db_logic = Logic(
                    logic_type="derivation",
                    solution_id=sol_id,
                    source_conclusion_ids=json.dumps(source_conc_ids),
                    source_prediction_ids=json.dumps(source_pred_ids),
                    chain_summary=chain_summary,
                    chain_type="recommendation",
                )
                session.add(db_logic)

            else:
                logger.warning(f"Unknown logic_type={el.logic_type!r}, skipping")

        # ── Step 8：标记帖子已处理 ──────────────────────────────────────
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        await session.commit()

        logger.info(
            f"RawPost {raw_post.id} processed: "
            f"{len(result.facts)} facts, "
            f"{len(result.conclusions)} conclusions, "
            f"{len(result.predictions)} predictions, "
            f"{len(result.assumptions)} assumptions, "
            f"{len(result.implicit_conditions)} implicit_conditions, "
            f"{len(result.solutions)} solutions, "
            f"{len(result.logics)} logics"
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
        logger.debug(f"LLM usage: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}")
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


async def _save_fact(
    session: AsyncSession,
    ef: ExtractedFact,
    raw_post_id: int | None = None,
) -> Fact:
    """创建 Fact + VerificationReference 并写入 session。"""
    validity_start = _parse_time_note(ef.validity_start_note)
    validity_end = _parse_time_note(ef.validity_end_note)

    fact = Fact(
        claim=ef.claim,
        canonical_claim=ef.canonical_claim,
        verifiable_statement=ef.verifiable_statement,
        temporal_type=ef.temporal_type,
        temporal_note=ef.temporal_note,
        verifiable_expression=ef.verifiable_expression,
        is_verifiable=ef.is_verifiable,
        verification_method=ef.verification_method,
        validity_start_note=ef.validity_start_note,
        validity_end_note=ef.validity_end_note,
        validity_start=validity_start,
        validity_end=validity_end,
        status=FactStatus.PENDING if ef.is_verifiable else FactStatus.UNVERIFIABLE,
        raw_post_id=raw_post_id,
    )
    session.add(fact)
    await session.flush()

    for ref in ef.suggested_references:
        session.add(VerificationReference(
            fact_id=fact.id,
            organization=ref.organization,
            data_description=ref.data_description,
            url=ref.url,
            url_note=ref.url_note,
        ))

    return fact


async def _save_assumption(
    session: AsyncSession,
    ea: ExtractedAssumption,
    raw_post_id: int | None = None,
) -> Assumption:
    """创建 Assumption 并写入 session。"""
    assump = Assumption(
        raw_post_id=raw_post_id,
        condition_text=ea.condition_text,
        canonical_condition=ea.canonical_condition,
        verifiable_statement=ea.verifiable_statement,
        temporal_type=ea.temporal_type,
        temporal_note=ea.temporal_note,
        is_verifiable=ea.is_verifiable,
    )
    session.add(assump)
    await session.flush()
    return assump


async def _save_implicit_condition(
    session: AsyncSession,
    eic: ExtractedImplicitCondition,
) -> ImplicitCondition:
    """创建 ImplicitCondition 并写入 session。"""
    ic = ImplicitCondition(
        condition_text=eic.condition_text,
        verifiable_statement=eic.verifiable_statement,
        temporal_type=eic.temporal_type,
        temporal_note=eic.temporal_note,
        is_consensus=eic.is_consensus,
        verification_result="consensus" if eic.is_consensus else "pending",
    )
    session.add(ic)
    await session.flush()
    return ic


def _build_chain_summary(
    chain_type: str,
    premise_labels: list[str],
    target_label: str,
) -> str:
    """程序化生成逻辑链自然语言摘要。"""
    if not premise_labels:
        premises_str = "（无前提）"
    else:
        # Truncate each label to keep summary readable
        truncated = [p[:50] + "…" if len(p) > 50 else p for p in premise_labels]
        premises_str = "、".join(f"[{p}]" for p in truncated)

    target_str = target_label[:60] + "…" if len(target_label) > 60 else target_label

    if chain_type == "inference":
        return f"由{premises_str}推断得到结论：[{target_str}]"
    elif chain_type == "prediction":
        return f"按照{premises_str}，可预测得到：[{target_str}]"
    elif chain_type == "recommendation":
        return f"基于{premises_str}，建议：[{target_str}]"
    else:
        return f"{premises_str} → [{target_str}]"


def _parse_time_note(note: str | None) -> datetime | None:
    if not note:
        return None
    from dateutil import parser as dateutil_parser
    try:
        return dateutil_parser.parse(note, fuzzy=True)
    except Exception:
        return None


async def _get_or_create_topic(session: AsyncSession, name: str) -> Topic:
    result = await session.exec(select(Topic).where(Topic.name == name))
    topic = result.first()
    if topic:
        return topic
    topic = Topic(name=name)
    session.add(topic)
    await session.flush()
    return topic


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
