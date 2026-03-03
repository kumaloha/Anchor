"""
Layer3 Step 4a — 预测型结论监控配置器（含条件型预测处理）
=========================================================
职责：
  1. 无时间段校验
     若预测型结论的 valid_until_note / time_horizon_note 均为空，
     则认为预测无效（缺乏可验证的时间窗口），跳过监控配置。

  2. 条件型预测分析
     若结论的 inference Logic 含非空 assumption_fact_indices（存在假设条件），
     则调用 LLM 评估假设条件及其概率：
       - negligible（极低概率）→ conditional_monitoring_status = "abandoned"，停止监控
       - low/medium/high        → conditional_monitoring_status = "waiting"，
                                  记录条件文本 + 照常配置监控窗口

  3. 标准监控配置
     对合格的预测型结论，确定权威信息源和监控时限（3-5年内可观测信号）。

结果写入 Conclusion 字段：
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
from anchor.models import Conclusion, Fact, Logic, _utcnow

_MAX_TOKENS_MONITOR = 768
_MAX_TOKENS_CONDITION = 512

# ---------------------------------------------------------------------------
# 系统提示 — 标准监控配置
# ---------------------------------------------------------------------------

_MONITOR_SYSTEM = """\
你是一名专业的预测核查分析师。给定一条预测型结论陈述，请：

1. 判断哪个权威信息源可用于验证这条结论是否成立
   可接受的权威来源：
   - 政府/监管机构（如国家统计局、财政部、央行、美联储、日本银行、ECB、BLS）
   - 国际金融机构（IMF、世界银行、BIS）
   - 主要交易所官方数据（NYSE、CME、上交所、港交所等）
   - 上市公司官方财报
   不接受：媒体评论、分析机构主观评级、个人判断

2. 确定监控时限：
   - 确定最早能对该结论作出有效判断的时间节点
   - 若结论时限模糊或超长，选取 **3-5 年内**可观测到明显信号的监控窗口
   - 给出人读的时段描述和机器可解析的起终点日期（ISO 8601 格式）

输出必须是合法的 JSON，不加任何其他文字。\
"""

_MONITOR_PROMPT = """\
## 待监控结论（预测型）

核心陈述：{claim}
时间范围说明：{time_horizon_note}
结论发布时间：{posted_at}
{condition_note}
## 任务

请分析这条预测型结论，确定验证它所需的权威信息源和监控时限。

**关键要求：**
- 即使结论时限很长（如"45年回本"），也请设定 3-5 年内可观测到显著信号的监控窗口
- 优先选择能持续更新的官方数据序列（如 FRED、央行数据库）
- monitoring_start 设为结论发布日期，monitoring_end 设为合理的评估截止日

严格输出 JSON：

```json
{{
  "monitoring_source_org": "监控机构名称（如'美联储 FRED'或'U.S. Treasury'）",
  "monitoring_source_url": "监控数据 URL（可确定时填写，否则填 null）",
  "monitoring_period_note": "人读的监控时段说明（如'2021-2026年30年期美债收益率走势'）",
  "monitoring_start": "监控起点 ISO 8601 日期（yyyy-mm-dd）",
  "monitoring_end": "监控终点 ISO 8601 日期（yyyy-mm-dd，建议设为3-5年后）",
  "reason": "一句话说明为何选择该来源和时限"
}}
```

若该结论完全无法量化或无任何可观测指标，monitoring_source_org 填"无法通过权威数据验证"，
其余监控字段填 null。\
"""

# ---------------------------------------------------------------------------
# 系统提示 — 条件型预测分析
# ---------------------------------------------------------------------------

_CONDITION_SYSTEM = """\
你是一名预测分析专家，专门评估带有假设条件的预测陈述。

给定一条预测结论及其依赖的假设条件（事实陈述），请：
1. 识别该预测的假设条件（"如果X则Y"中的X），用简洁语言描述
2. 评估该假设条件在现实中发生的概率：
   - high        — 概率较高（>50%），如正在进行的政策趋势、已基本确定的事件
   - medium      — 中等概率（20%-50%），有争议但并非罕见
   - low         — 概率较低（5%-20%），不太可能但不是极端罕见
   - negligible  — 极低概率（<5%），极端罕见场景（如假设核战争、某国解体等）

输出必须是合法的 JSON，不加任何其他文字。\
"""

_CONDITION_PROMPT = """\
## 预测结论

{conclusion_claim}

## 该预测依赖的假设条件（事实陈述）

{assumption_claims}

## 任务

请分析上述假设条件，判断其现实发生概率。

严格输出 JSON：

```json
{{
  "conditional_assumption": "简洁描述假设条件（≤50字，如'台湾海峡爆发武装冲突'）",
  "assumption_probability": "high|medium|low|negligible",
  "probability_reason": "一句话说明概率判断依据（≤60字）"
}}
```\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class ConclusionMonitor:
    """为预测型结论配置监控信息（Layer3 Step 4a）。"""

    async def setup(self, conclusion: Conclusion, session: AsyncSession) -> None:
        """分析预测型结论，设置监控字段。

        仅处理 conclusion_type == "predictive" 的记录。
        """
        if conclusion.conclusion_type != "predictive":
            logger.debug(
                f"[ConclusionMonitor] conclusion id={conclusion.id} "
                f"type={conclusion.conclusion_type}, skip (not predictive)"
            )
            return

        # ── 校验一：无时间段 → 预测无效 ──────────────────────────────────────
        has_time = bool(
            (conclusion.time_horizon_note and conclusion.time_horizon_note.strip())
            or conclusion.valid_until
        )
        if not has_time:
            logger.warning(
                f"[ConclusionMonitor] conclusion id={conclusion.id} "
                "predictive 但未提供时间范围，跳过监控（预测无效）"
            )
            conclusion.monitoring_source_org = "无时间范围，预测无效"
            conclusion.monitoring_period_note = "作者未给出预测时间段，无法建立监控窗口"
            session.add(conclusion)
            await session.flush()
            return

        # ── 校验二：条件型预测分析 ────────────────────────────────────────────
        await self._analyze_conditional(conclusion, session)

        # 若假设极低概率，放弃监控
        if conclusion.conditional_monitoring_status == "abandoned":
            logger.info(
                f"[ConclusionMonitor] conclusion id={conclusion.id} "
                "条件型预测（极低概率假设），放弃监控"
            )
            conclusion.monitoring_source_org = "放弃监控：假设条件发生概率极低"
            assumption_text = conclusion.conditional_assumption or "（未知条件）"
            conclusion.monitoring_period_note = (
                f"假设条件 [{assumption_text}] 发生概率为 negligible，无需监控"
            )
            session.add(conclusion)
            await session.flush()
            return

        # ── 标准监控配置 ──────────────────────────────────────────────────────
        condition_note = ""
        if conclusion.conditional_monitoring_status == "waiting":
            assumption_text = conclusion.conditional_assumption or "（未知条件）"
            condition_note = (
                f"\n注意：此为条件型预测，假设条件为 [{assumption_text}]。\n"
                "请针对该结论本身配置监控窗口（同时需要等待假设条件触发）。\n"
            )

        prompt = _MONITOR_PROMPT.format(
            claim=conclusion.claim,
            time_horizon_note=(
                conclusion.time_horizon_note or str(conclusion.valid_until) or "（未指定）"
            ),
            posted_at=(
                conclusion.posted_at.strftime("%Y-%m-%d")
                if conclusion.posted_at else "未知"
            ),
            condition_note=condition_note,
        )

        resp = await chat_completion(
            system=_MONITOR_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_MONITOR,
        )
        if resp is None:
            logger.warning(
                f"[ConclusionMonitor] LLM call failed for conclusion id={conclusion.id}"
            )
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(
                f"[ConclusionMonitor] Parse failed for conclusion id={conclusion.id}"
            )
            return

        conclusion.monitoring_source_org  = parsed.get("monitoring_source_org")
        conclusion.monitoring_source_url  = parsed.get("monitoring_source_url")
        conclusion.monitoring_period_note = parsed.get("monitoring_period_note")
        conclusion.monitoring_start       = _parse_date(parsed.get("monitoring_start"))
        conclusion.monitoring_end         = _parse_date(parsed.get("monitoring_end"))
        session.add(conclusion)
        await session.flush()

        logger.info(
            f"[ConclusionMonitor] conclusion id={conclusion.id} → "
            f"org={conclusion.monitoring_source_org} | "
            f"period={conclusion.monitoring_period_note} | "
            f"conditional_status={conclusion.conditional_monitoring_status}"
        )

    # ── 条件型预测分析 ────────────────────────────────────────────────────────

    async def _analyze_conditional(
        self, conclusion: Conclusion, session: AsyncSession
    ) -> None:
        """检查是否为条件型预测，若是则评估假设概率并更新相关字段。"""
        # 加载 inference Logic，检查 assumption_fact_indices
        logic_result = await session.exec(
            select(Logic).where(
                Logic.logic_type == "inference",
                Logic.conclusion_id == conclusion.id,
            )
        )
        inference_logic = logic_result.first()

        if not inference_logic:
            conclusion.conditional_monitoring_status = "not_applicable"
            return

        assumption_ids: list[int] = []
        try:
            assumption_ids = json.loads(inference_logic.assumption_fact_ids or "[]")
        except Exception:
            pass

        if not assumption_ids:
            conclusion.conditional_monitoring_status = "not_applicable"
            return

        # 加载假设条件事实
        facts_result = await session.exec(
            select(Fact).where(Fact.id.in_(assumption_ids))
        )
        assumption_facts = list(facts_result.all())

        if not assumption_facts:
            conclusion.conditional_monitoring_status = "not_applicable"
            return

        # 构建假设条件描述
        assumption_claims = "\n".join(
            f"  {i + 1}. {f.claim}" for i, f in enumerate(assumption_facts)
        )

        prompt = _CONDITION_PROMPT.format(
            conclusion_claim=conclusion.claim,
            assumption_claims=assumption_claims,
        )

        resp = await chat_completion(
            system=_CONDITION_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_CONDITION,
        )

        if resp is None:
            logger.warning(
                f"[ConclusionMonitor] 条件分析 LLM 失败 conclusion id={conclusion.id}"
            )
            conclusion.conditional_monitoring_status = "not_applicable"
            return

        parsed = _parse_json(resp.content)
        if not parsed:
            conclusion.conditional_monitoring_status = "not_applicable"
            return

        prob = parsed.get("assumption_probability", "medium")
        conclusion.conditional_assumption  = parsed.get("conditional_assumption")
        conclusion.assumption_probability  = prob

        if prob == "negligible":
            conclusion.conditional_monitoring_status = "abandoned"
        else:
            conclusion.conditional_monitoring_status = "waiting"

        logger.info(
            f"[ConclusionMonitor] conclusion id={conclusion.id} "
            f"条件型预测: assumption='{conclusion.conditional_assumption}' "
            f"prob={prob} status={conclusion.conditional_monitoring_status}"
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _parse_json(raw: str) -> dict | None:
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
        logger.warning(f"[ConclusionMonitor] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
