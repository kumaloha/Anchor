"""
Layer2 — 观点提取器
====================
输入：一条原始帖子（RawPost）
输出：写入数据库的 Fact / Conclusion / Solution / Logic 记录

写库顺序（Logic 依赖前三者的 DB ID）：
  1. 批量创建 Fact → 收集 {local_idx: db_fact_id} 映射
  2a. 创建 Conclusion → 收集 ID 映射
  2b. 创建 Solution → 收集 ID 映射
  3. 创建 Logic，将 local indices 转为真实 DB ID
     - inference 类型：conclusion_id + supporting/assumption_fact_ids
     - derivation 类型：solution_id + source_conclusion_ids (JSON)
  4. 更新 RawPost.is_processed
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.classifier.prompts import PROMPT_REGISTRY, DEFAULT_PROMPT_VERSION
from anchor.classifier.schemas import ExtractionResult, ExtractedFact
from anchor.llm_client import chat_completion
from anchor.models import (
    Author,
    Conclusion,
    ConclusionStatus,
    Fact,
    FactStatus,
    Logic,
    RawPost,
    Solution,
    SolutionStatus,
    Topic,
    VerificationReference,
    _utcnow,
)

_MAX_TOKENS = 8000


class Extractor:
    """观点提取器

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

        # ── Step 1：创建所有 Fact，建立 local_idx → db_id 映射 ──────────────
        fact_id_map: dict[int, int] = {}   # {local_idx: db_fact_id}
        for idx, ef in enumerate(result.facts):
            db_fact = await _save_fact(session, ef, raw_post_id=raw_post.id)
            fact_id_map[idx] = db_fact.id

        # ── Step 2a：创建 Conclusion，建立映射 ───────────────────────────────
        conclusion_id_map: dict[int, int] = {}
        for idx, ec in enumerate(result.conclusions):
            topic = await _get_or_create_topic(session, ec.topic)
            valid_from = _parse_time_note(ec.time_horizon_note)
            valid_until = _parse_time_note(ec.valid_until_note)
            db_conc = Conclusion(
                topic_id=topic.id,
                author_id=author.id,
                claim=ec.claim,
                canonical_claim=ec.canonical_claim,
                conclusion_type=ec.conclusion_type,
                time_horizon_note=ec.time_horizon_note,
                valid_from=valid_from,
                valid_until=valid_until,
                source_url=raw_post.url,
                source_platform=raw_post.source,
                posted_at=raw_post.posted_at,
                raw_extraction=raw_extraction_str,
            )
            session.add(db_conc)
            await session.flush()
            conclusion_id_map[idx] = db_conc.id

        # ── Step 2b：创建 Solution，建立映射 ─────────────────────────────────
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

        # ── Step 3：创建 Logic，local indices → DB IDs ───────────────────────
        for el in result.logics:
            if el.logic_type == "inference":
                conc_id = conclusion_id_map.get(el.target_index) if el.target_index is not None else None
                if conc_id is None:
                    logger.warning(
                        f"Logic inference target_index={el.target_index} not found, skipping"
                    )
                    continue

                supporting_ids = [
                    fact_id_map[i] for i in el.supporting_fact_indices
                    if i in fact_id_map
                ]
                assumption_ids = [
                    fact_id_map[i] for i in el.assumption_fact_indices
                    if i in fact_id_map
                ]

                db_logic = Logic(
                    logic_type="inference",
                    conclusion_id=conc_id,
                    supporting_fact_ids=json.dumps(supporting_ids),
                    assumption_fact_ids=json.dumps(assumption_ids),
                )
                session.add(db_logic)

            elif el.logic_type == "derivation":
                sol_id = solution_id_map.get(el.solution_index) if el.solution_index is not None else None
                if sol_id is None:
                    logger.warning(
                        f"Logic derivation solution_index={el.solution_index} not found, skipping"
                    )
                    continue

                source_conc_ids = [
                    conclusion_id_map[i] for i in el.source_conclusion_indices
                    if i in conclusion_id_map
                ]

                db_logic = Logic(
                    logic_type="derivation",
                    solution_id=sol_id,
                    source_conclusion_ids=json.dumps(source_conc_ids),
                )
                session.add(db_logic)

            else:
                logger.warning(f"Unknown logic_type={el.logic_type!r}, skipping")

        # ── Step 4：标记帖子已处理 ────────────────────────────────────────────
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        await session.commit()

        logger.info(
            f"RawPost {raw_post.id} processed: "
            f"{len(result.facts)} facts, "
            f"{len(result.conclusions)} conclusions, "
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
