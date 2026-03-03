"""
Layer3 Step 3 — 预测监控配置器（含条件型预测处理）
====================================================
为 Prediction 实体配置监控信息（改自 conclusion_monitor.py）。

职责：
  1. 无时间段校验 — temporal_note 为空则认为预测无效
  2. 条件型预测分析 — 评估假设条件概率
  3. 标准监控配置

结果写入 Prediction 字段：
  monitoring_source_org / url / period_note / start / end
  conditional_assumption / assumption_probability / conditional_monitoring_status
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Assumption, Fact, Logic, Prediction, _utcnow

_MAX_TOKENS_MONITOR = 768
_MAX_TOKENS_CONDITION = 512

_MONITOR_SYSTEM = (
    "你是一名专业的预测核查分析师。给定一条预测型陈述，确定验证它所需的权威信息源和监控时限。"
    "选取 3-5 年内可观测到明显信号的监控窗口。"
    "输出合法 JSON，不加任何其他文字。"
)

_CONDITION_SYSTEM = (
    "你是一名预测分析专家，评估带有假设条件的预测。"
    "给定一条预测及其假设条件，评估假设条件在现实中的发生概率："
    "high(>50%) / medium(20-50%) / low(5-20%) / negligible(<5%)。"
    "输出合法 JSON，不加其他文字。"
)


class PredictionMonitor:
    """为 Prediction 配置监控信息（Layer3 Step 3）。"""

    async def setup(self, prediction: Prediction, session: AsyncSession) -> None:
        """分析预测，设置监控字段。"""
        # 校验：无时间段 → 预测无效
        has_time = bool(prediction.temporal_note and prediction.temporal_note.strip())
        if not has_time:
            logger.warning(
                f"[PredictionMonitor] prediction id={prediction.id} 无时间范围，跳过监控"
            )
            prediction.monitoring_source_org = "无时间范围，预测无效"
            prediction.monitoring_period_note = "作者未给出预测时间段，无法建立监控窗口"
            session.add(prediction)
            await session.flush()
            return

        # 条件型预测分析
        await self._analyze_conditional(prediction, session)

        if prediction.conditional_monitoring_status == "abandoned":
            logger.info(
                f"[PredictionMonitor] prediction id={prediction.id} "
                "条件型预测（极低概率），放弃监控"
            )
            prediction.monitoring_source_org = "放弃监控：假设条件发生概率极低"
            assumption_text = prediction.conditional_assumption or "（未知条件）"
            prediction.monitoring_period_note = (
                f"假设条件 [{assumption_text}] negligible，无需监控"
            )
            session.add(prediction)
            await session.flush()
            return

        # 标准监控配置
        condition_note = ""
        if prediction.conditional_monitoring_status == "waiting":
            assumption_text = prediction.conditional_assumption or "（未知条件）"
            condition_note = f"注意：条件型预测，假设条件为 [{assumption_text}]。\n"

        posted_at_str = (
            prediction.posted_at.strftime("%Y-%m-%d") if prediction.posted_at else "未知"
        )
        prompt = (
            f"## 待监控预测\n核心陈述：{prediction.claim}\n"
            f"时间范围：{prediction.temporal_note or '（未指定）'}\n"
            f"发布时间：{posted_at_str}\n{condition_note}\n"
            "请确定验证此预测的权威信息源和监控时限。\n"
            "monitoring_start 设为发布日期，monitoring_end 设为3-5年后。\n"
            "严格输出 JSON（不加其他文字）：\n"
            "{\n"
            '  "monitoring_source_org": "监控机构名称",\n'
            '  "monitoring_source_url": null,\n'
            '  "monitoring_period_note": "人读的监控时段说明",\n'
            '  "monitoring_start": "yyyy-mm-dd",\n'
            '  "monitoring_end": "yyyy-mm-dd",\n'
            '  "reason": "一句话说明"\n'
            "}"
        )

        resp = await chat_completion(
            system=_MONITOR_SYSTEM, user=prompt, max_tokens=_MAX_TOKENS_MONITOR
        )
        if resp is None:
            logger.warning(
                f"[PredictionMonitor] LLM call failed for prediction id={prediction.id}"
            )
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(
                f"[PredictionMonitor] Parse failed for prediction id={prediction.id}"
            )
            return

        prediction.monitoring_source_org  = parsed.get("monitoring_source_org")
        prediction.monitoring_source_url  = parsed.get("monitoring_source_url")
        prediction.monitoring_period_note = parsed.get("monitoring_period_note")
        prediction.monitoring_start       = _parse_date(parsed.get("monitoring_start"))
        prediction.monitoring_end         = _parse_date(parsed.get("monitoring_end"))
        session.add(prediction)
        await session.flush()

        logger.info(
            f"[PredictionMonitor] prediction id={prediction.id} → "
            f"org={prediction.monitoring_source_org} | "
            f"period={prediction.monitoring_period_note}"
        )

    async def _analyze_conditional(
        self, prediction: Prediction, session: AsyncSession
    ) -> None:
        logic_result = await session.exec(
            select(Logic).where(
                Logic.logic_type == "prediction",
                Logic.prediction_id == prediction.id,
            )
        )
        pred_logic = logic_result.first()

        if not pred_logic:
            prediction.conditional_monitoring_status = "not_applicable"
            return

        assumption_fact_ids: list[int] = []
        assump_ids: list[int] = []
        try:
            assumption_fact_ids = json.loads(pred_logic.assumption_fact_ids or "[]")
        except Exception:
            pass
        try:
            assump_ids = json.loads(pred_logic.assumption_ids or "[]")
        except Exception:
            pass

        if not assumption_fact_ids and not assump_ids:
            prediction.conditional_monitoring_status = "not_applicable"
            return

        assumption_texts: list[str] = []
        if assumption_fact_ids:
            facts_result = await session.exec(
                select(Fact).where(Fact.id.in_(assumption_fact_ids))
            )
            for f in facts_result.all():
                assumption_texts.append(f.claim)
        if assump_ids:
            assump_result = await session.exec(
                select(Assumption).where(Assumption.id.in_(assump_ids))
            )
            for a in assump_result.all():
                assumption_texts.append(a.condition_text)

        if not assumption_texts:
            prediction.conditional_monitoring_status = "not_applicable"
            return

        assumption_claims = "\n".join(
            f"  {i+1}. {t}" for i, t in enumerate(assumption_texts)
        )
        prompt = (
            f"## 预测陈述\n{prediction.claim}\n\n"
            f"## 假设条件\n{assumption_claims}\n\n"
            "评估假设条件在现实中的发生概率，输出 JSON：\n"
            "{\n"
            '  "conditional_assumption": "简洁描述假设条件（≤50字）",\n'
            '  "assumption_probability": "high|medium|low|negligible",\n'
            '  "probability_reason": "一句话说明（≤60字）"\n'
            "}"
        )

        resp = await chat_completion(
            system=_CONDITION_SYSTEM, user=prompt, max_tokens=_MAX_TOKENS_CONDITION
        )
        if resp is None:
            prediction.conditional_monitoring_status = "not_applicable"
            return

        parsed = _parse_json(resp.content)
        if not parsed:
            prediction.conditional_monitoring_status = "not_applicable"
            return

        prob = parsed.get("assumption_probability", "medium")
        prediction.conditional_assumption = parsed.get("conditional_assumption")
        prediction.assumption_probability = prob
        prediction.conditional_monitoring_status = (
            "abandoned" if prob == "negligible" else "waiting"
        )

        logger.info(
            f"[PredictionMonitor] prediction id={prediction.id} "
            f"conditional: assumption={prediction.conditional_assumption!r} prob={prob}"
        )


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _parse_json(raw: str) -> dict | None:
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("{")
        end   = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[PredictionMonitor] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
