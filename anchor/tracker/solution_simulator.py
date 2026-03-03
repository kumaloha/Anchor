"""
Layer3 Step 4b — 解决方案模拟器（含基准价格记录）
=================================================
对每个 PENDING Solution，通过 LLM 两阶段分析：

  Phase A: 模拟执行
    生成 simulated_action_note（≤100字），描述"假设今日执行此建议意味着什么"

  Phase B: 监控配置
    确定验证此建议效果所需的权威信息源（仅接受 Tier1/Tier2）和监控时限

  Phase C: 基准价格记录
    通过 Tavily 搜索，查询 action_target 在发布时刻的实际价格/数值，
    作为未来对比的基准（stored in baseline_value / baseline_metric）。

结果写入：
  Solution.simulated_action_note
  Solution.monitoring_source_org / url / period_note / start / end
  Solution.baseline_value / baseline_metric / baseline_recorded_at
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Conclusion, Logic, Solution, _utcnow

_MAX_TOKENS_SIMULATE = 1024
_MAX_TOKENS_BASELINE = 512

_SIMULATE_SYSTEM = """\
你是一名专业的投资建议评估师。给定一条具体的投资行动建议及其所基于的结论，请：

Phase A — 模拟执行：
  用一句话（≤100字）描述"假设今日按此建议操作，具体意味着什么"
  包含：行动方向 + 标的 + 大致仓位规模建议（若可判断）+ 时间维度

Phase B — 监控配置：
  确定验证此建议效果的最佳权威数据源和监控时限

可接受的监控来源：
- Tier1：政府/监管机构、央行、上市公司财报（权威机构直接数据）
- Tier2：主要交易所官方价格数据、指数数据（金融市场数据）
不接受：媒体评论、分析机构预测报告

输出必须是合法的 JSON，不加任何其他文字。\
"""

_SIMULATE_PROMPT = """\
## 投资行动建议

建议内容：{claim}
行动类型：{action_type}
行动标的：{action_target}
推导依据：{action_rationale}

## 所基于的结论

{conclusions_section}

## 任务

**Phase A — 模拟执行（≤100字）：**
假设今日按此建议执行，具体意味着什么？
请描述：行动方向、标的、关键时间维度、预期观测指标

**Phase B — 监控配置：**
- 最佳监控数据源（Tier1优先：政府数据/财报；Tier2：交易所价格数据）
- 监控时限：建议执行日至能观测到明显结果的合理截止日

严格输出 JSON：

```json
{{
  "simulated_action_note": "≤100字的模拟执行描述",
  "monitoring_source_org": "监控机构名称（Tier1优先）",
  "monitoring_source_url": "监控数据 URL（可确定时填写，否则null）",
  "monitoring_period_note": "人读的监控时段说明",
  "monitoring_start": "监控起点 ISO 8601 日期（yyyy-mm-dd）",
  "monitoring_end": "监控终点 ISO 8601 日期（yyyy-mm-dd）",
  "reason": "一句话说明监控方案选择理由"
}}
```

若该建议完全无法通过权威数据验证效果，simulated_action_note 仍须填写，
monitoring_source_org 填"无法通过权威数据验证"，其余监控字段填 null。\
"""

# ---------------------------------------------------------------------------
# 基准价格查询提示
# ---------------------------------------------------------------------------

_BASELINE_SYSTEM = """\
你是一名金融数据分析助手。给定一个投资标的，请根据提供的搜索结果，
提取当前（或最近）的市场价格/数值作为基准。

输出必须是合法的 JSON，不加任何其他文字。\
"""

_BASELINE_PROMPT_WITH_SEARCH = """\
今日日期：{today}

## 投资标的

标的：{action_target}
行动类型：{action_type}

## 搜索结果

{search_section}

## 任务

请从搜索结果中提取该标的的最新价格/数值，作为发布时刻的基准。

严格输出 JSON：

```json
{{
  "baseline_value": "当前价格或数值（含单位，如'2650 USD/oz'或'4800点'）",
  "baseline_metric": "指标说明（如'黄金现货价 USD/oz'、'标普500指数'）",
  "data_date": "数据日期 yyyy-mm-dd（若能确定）"
}}
```

若无法从搜索结果中获取有效价格，baseline_value 填 null，baseline_metric 填 null。\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class SolutionSimulator:
    """为解决方案模拟执行并配置监控（Layer3 Step 4b）。"""

    async def simulate(self, solution: Solution, session: AsyncSession) -> None:
        """分析解决方案，写入 simulated_action_note、监控字段和基准价格。"""

        # 加载相关结论（从 derivation Logic 中获取 source_conclusion_ids）
        logic_result = await session.exec(
            select(Logic).where(
                Logic.logic_type == "derivation",
                Logic.solution_id == solution.id,
            )
        )
        derivation_logic = logic_result.first()

        conclusion_texts: list[str] = []
        if derivation_logic and derivation_logic.source_conclusion_ids:
            try:
                conc_ids = json.loads(derivation_logic.source_conclusion_ids)
            except Exception:
                conc_ids = []

            if conc_ids:
                conc_result = await session.exec(
                    select(Conclusion).where(Conclusion.id.in_(conc_ids))
                )
                for c in conc_result.all():
                    type_label = "回顾型" if c.conclusion_type == "retrospective" else "预测型"
                    conclusion_texts.append(f"- [{type_label}] {c.claim}")

        conclusions_section = "\n".join(conclusion_texts) if conclusion_texts else "（未找到关联结论）"

        prompt = _SIMULATE_PROMPT.format(
            claim=solution.claim,
            action_type=solution.action_type or "（未指定）",
            action_target=solution.action_target or "（未指定）",
            action_rationale=solution.action_rationale or "（未说明）",
            conclusions_section=conclusions_section,
        )

        resp = await chat_completion(
            system=_SIMULATE_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_SIMULATE,
        )
        if resp is None:
            logger.warning(f"[SolutionSimulator] LLM call failed for solution id={solution.id}")
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[SolutionSimulator] Parse failed for solution id={solution.id}")
            return

        solution.simulated_action_note  = parsed.get("simulated_action_note")
        solution.monitoring_source_org  = parsed.get("monitoring_source_org")
        solution.monitoring_source_url  = parsed.get("monitoring_source_url")
        solution.monitoring_period_note = parsed.get("monitoring_period_note")
        solution.monitoring_start       = _parse_date(parsed.get("monitoring_start"))
        solution.monitoring_end         = _parse_date(parsed.get("monitoring_end"))
        session.add(solution)
        await session.flush()

        logger.info(
            f"[SolutionSimulator] solution id={solution.id} → "
            f"simulated={solution.simulated_action_note and solution.simulated_action_note[:40]} | "
            f"org={solution.monitoring_source_org}"
        )

        # Phase C: 记录基准价格
        if solution.action_target:
            await self._record_baseline(solution, session)

    # ── 基准价格记录 ──────────────────────────────────────────────────────────

    async def _record_baseline(self, solution: Solution, session: AsyncSession) -> None:
        """查询并记录标的在发布时刻的基准价格。"""
        from datetime import date
        from anchor.tracker.web_searcher import web_search, format_search_results

        today_str = date.today().isoformat()
        target = solution.action_target

        # 构建价格搜索查询
        search_query = f"{target} current price {today_str}"
        if solution.action_type in ("buy", "sell", "hold", "short"):
            search_query = f"{target} price today {today_str}"

        search_results = await web_search(search_query, max_results=3)
        if not search_results:
            logger.debug(
                f"[SolutionSimulator] 无搜索结果，跳过基准价格记录 "
                f"solution id={solution.id} target={target}"
            )
            return

        search_section = format_search_results(search_results)
        prompt = _BASELINE_PROMPT_WITH_SEARCH.format(
            today=today_str,
            action_target=target,
            action_type=solution.action_type or "（未指定）",
            search_section=search_section,
        )

        resp = await chat_completion(
            system=_BASELINE_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_BASELINE,
        )
        if resp is None:
            return

        parsed = _parse_json(resp.content)
        if not parsed:
            return

        baseline_value = parsed.get("baseline_value")
        if not baseline_value:
            logger.debug(
                f"[SolutionSimulator] LLM 未能提取基准价格 solution id={solution.id}"
            )
            return

        solution.baseline_value      = baseline_value
        solution.baseline_metric     = parsed.get("baseline_metric")
        solution.baseline_recorded_at = _utcnow()
        session.add(solution)
        await session.flush()

        logger.info(
            f"[SolutionSimulator] solution id={solution.id} "
            f"基准价格: {solution.baseline_metric}={solution.baseline_value}"
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
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[SolutionSimulator] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
