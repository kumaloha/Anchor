"""
Layer3 Step 2 — 现实对齐器
============================
对 Fact / Conclusion / ImplicitCondition / Assumption 进行现实核查。
Prediction 仅在 monitoring_end 到达后运行。

流程：
  1. 提取 entity.verifiable_statement + temporal_note 构建搜索查询
  2. Tavily 搜索（若有 API Key）
  3. LLM 判断陈述是否与现实对齐
  4. 写入 alignment_result / alignment_evidence / alignment_tier /
     alignment_confidence / alignment_verified_at（inline 字段）

alignment_result 取值：
  true        — 与现实对齐
  false       — 与现实不符
  uncertain   — 证据不足以判断
  unavailable — 超出知识截止日期或无法获取证据
"""

from __future__ import annotations

import json
import re
from typing import Union

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Assumption,
    Conclusion,
    Fact,
    ImplicitCondition,
    Prediction,
    _utcnow,
)
from anchor.tracker.web_searcher import web_search, format_search_results

_MAX_TOKENS = 1024

_SYSTEM = """\
你是一名专业事实核查员。给定一条陈述，结合搜索结果和训练知识，判断该陈述是否与现实对齐。

输出必须是合法的 JSON，格式：
{
  "alignment_result": "true|false|uncertain|unavailable",
  "alignment_evidence": "支持判断的证据摘要（≤200字）",
  "alignment_tier": 1,
  "alignment_confidence": "high|medium|low"
}

alignment_result:
  true        — 陈述与现实对齐，有充分证据支持
  false       — 陈述与现实不符，有证据反驳
  uncertain   — 证据不足或存在争议，无法明确判断
  unavailable — 超出知识截止日期，或无法获取足够证据

alignment_tier（证据质量）:
  1 — 权威机构官方数据（政府、央行、国际组织）
  2 — 金融市场数据或主要媒体引用官方数据
  3 — 其他可信第三方来源
  null — 仅依赖训练知识，无外部来源

alignment_confidence: high/medium/low
不加任何其他文字。\
"""

# Valid alignment result values
_VALID_RESULTS = {"true", "false", "uncertain", "unavailable"}


class RealityAligner:
    """对实体的 verifiable_statement 进行现实对齐验证（Layer3 Step 2）。"""

    async def align_fact(self, fact: Fact, session: AsyncSession) -> bool:
        """对 Fact 进行现实对齐。"""
        if fact.alignment_verified_at is not None:
            logger.debug(f"[RealityAligner] Fact id={fact.id} already aligned, skipping")
            return False

        stmt = fact.verifiable_statement or fact.verifiable_expression or fact.claim
        if not stmt:
            return False

        query = _build_query(stmt, fact.temporal_note)
        result = await self._run_alignment(stmt, query, fact.temporal_note)
        if result is None:
            return False

        fact.alignment_result = result["alignment_result"]
        fact.alignment_evidence = result["alignment_evidence"]
        fact.alignment_tier = result["alignment_tier"]
        fact.alignment_confidence = result["alignment_confidence"]
        fact.alignment_verified_at = _utcnow()
        session.add(fact)

        logger.info(
            f"[RealityAligner] Fact id={fact.id} → {fact.alignment_result} "
            f"(tier={fact.alignment_tier})"
        )
        return True

    async def align_conclusion(self, conclusion: Conclusion, session: AsyncSession) -> bool:
        """对 Conclusion 进行现实对齐。"""
        if conclusion.alignment_verified_at is not None:
            logger.debug(f"[RealityAligner] Conclusion id={conclusion.id} already aligned, skipping")
            return False

        stmt = conclusion.verifiable_statement or conclusion.claim
        if not stmt:
            return False

        query = _build_query(stmt, conclusion.temporal_note or conclusion.time_horizon_note)
        result = await self._run_alignment(stmt, query, conclusion.temporal_note)
        if result is None:
            return False

        conclusion.alignment_result = result["alignment_result"]
        conclusion.alignment_evidence = result["alignment_evidence"]
        conclusion.alignment_tier = result["alignment_tier"]
        conclusion.alignment_confidence = result["alignment_confidence"]
        conclusion.alignment_verified_at = _utcnow()
        session.add(conclusion)

        logger.info(
            f"[RealityAligner] Conclusion id={conclusion.id} → {conclusion.alignment_result}"
        )
        return True

    async def align_implicit_condition(
        self, ic: ImplicitCondition, session: AsyncSession
    ) -> bool:
        """对 ImplicitCondition 进行现实对齐。"""
        if ic.alignment_verified_at is not None:
            return False

        # Skip consensus conditions (assumed true by definition)
        if ic.is_consensus:
            ic.alignment_result = "true"
            ic.alignment_evidence = "普遍共识，无需验证"
            ic.alignment_tier = None
            ic.alignment_confidence = "high"
            ic.alignment_verified_at = _utcnow()
            session.add(ic)
            return True

        stmt = ic.verifiable_statement or ic.condition_text
        if not stmt:
            return False

        query = _build_query(stmt, ic.temporal_note)
        result = await self._run_alignment(stmt, query, ic.temporal_note)
        if result is None:
            return False

        ic.alignment_result = result["alignment_result"]
        ic.alignment_evidence = result["alignment_evidence"]
        ic.alignment_tier = result["alignment_tier"]
        ic.alignment_confidence = result["alignment_confidence"]
        ic.alignment_verified_at = _utcnow()
        session.add(ic)

        logger.info(
            f"[RealityAligner] ImplicitCondition id={ic.id} → {ic.alignment_result}"
        )
        return True

    async def align_assumption(self, assumption: Assumption, session: AsyncSession) -> bool:
        """对 Assumption 进行现实对齐。"""
        if assumption.alignment_verified_at is not None:
            return False

        stmt = assumption.verifiable_statement or assumption.condition_text
        if not stmt:
            return False

        query = _build_query(stmt, assumption.temporal_note)
        result = await self._run_alignment(stmt, query, assumption.temporal_note)
        if result is None:
            return False

        assumption.alignment_result = result["alignment_result"]
        assumption.alignment_evidence = result["alignment_evidence"]
        assumption.alignment_tier = result["alignment_tier"]
        assumption.alignment_confidence = result["alignment_confidence"]
        assumption.alignment_verified_at = _utcnow()
        session.add(assumption)

        logger.info(
            f"[RealityAligner] Assumption id={assumption.id} → {assumption.alignment_result}"
        )
        return True

    async def align_prediction(self, prediction: Prediction, session: AsyncSession) -> bool:
        """对 Prediction 进行现实对齐（仅在 monitoring_end 到达后运行）。"""
        if prediction.alignment_verified_at is not None:
            return False

        now = _utcnow()
        if prediction.monitoring_end and now < prediction.monitoring_end:
            logger.debug(
                f"[RealityAligner] Prediction id={prediction.id} "
                f"monitoring_end={prediction.monitoring_end} not reached, skipping"
            )
            return False

        stmt = prediction.verifiable_statement or prediction.claim
        if not stmt:
            return False

        query = _build_query(stmt, prediction.temporal_note)
        result = await self._run_alignment(stmt, query, prediction.temporal_note)
        if result is None:
            return False

        prediction.alignment_result = result["alignment_result"]
        prediction.alignment_evidence = result["alignment_evidence"]
        prediction.alignment_tier = result["alignment_tier"]
        prediction.alignment_confidence = result["alignment_confidence"]
        prediction.alignment_verified_at = _utcnow()
        session.add(prediction)

        logger.info(
            f"[RealityAligner] Prediction id={prediction.id} → {prediction.alignment_result}"
        )
        return True

    async def _run_alignment(
        self,
        statement: str,
        query: str,
        temporal_note: str | None,
    ) -> dict | None:
        """执行对齐验证：搜索 + LLM 判断。"""
        # Tavily search
        search_results = await web_search(query, max_results=4)
        search_text = ""
        if search_results:
            search_text = f"\n\n## 搜索结果\n\n{format_search_results(search_results)}"

        time_context = f"\n时间范围：{temporal_note}" if temporal_note else ""

        prompt = f"""\
请判断以下陈述是否与现实对齐：

## 待核查陈述
{statement}{time_context}{search_text}

请基于搜索结果（优先）和训练知识判断陈述真伪。
严格输出 JSON，不加任何其他文字。
"""

        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            return None

        return _parse_response(resp.content)


def _build_query(statement: str, temporal_note: str | None) -> str:
    """构建搜索查询。"""
    base = statement[:200] if len(statement) > 200 else statement
    if temporal_note:
        return f"{base} {temporal_note}"
    return base


def _parse_response(raw: str) -> dict | None:
    """从 LLM 输出解析 JSON。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = raw.strip()
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)

        # Normalize alignment_result
        result = data.get("alignment_result", "uncertain")
        if result not in _VALID_RESULTS:
            # Common aliases
            aliases = {
                "unverifiable": "unavailable",
                "unknown": "unavailable",
                "n/a": "unavailable",
            }
            result = aliases.get(result.lower() if isinstance(result, str) else "", "uncertain")
        data["alignment_result"] = result

        # Normalize alignment_tier
        tier = data.get("alignment_tier")
        if tier not in (1, 2, 3, None):
            data["alignment_tier"] = None

        # Ensure alignment_confidence
        conf = data.get("alignment_confidence", "medium")
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        data["alignment_confidence"] = conf

        # Ensure alignment_evidence
        if not data.get("alignment_evidence"):
            data["alignment_evidence"] = None

        return data
    except Exception as exc:
        logger.warning(f"[RealityAligner] JSON parse error: {exc}")
        return None
