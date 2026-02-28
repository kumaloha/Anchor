"""
Layer3 Step 4b — 解决方案模拟器
=================================
对每个 PENDING Solution，通过 LLM 两阶段分析：

  Phase A: 模拟执行
    基于 solution.claim + action_type + action_target + 相关结论内容，
    生成 simulated_action_note（≤100字），描述"假设现在执行此建议意味着什么"

  Phase B: 监控配置
    确定验证此建议效果所需的权威信息源（仅接受 Tier1/Tier2）和监控时限

结果写入：
  Solution.simulated_action_note
  Solution.monitoring_source_org / url / period_note / start / end
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

_MAX_TOKENS = 1024

_SYSTEM_PROMPT = """\
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

输出必须是合法的 JSON，不加任何其他文字。
"""

_PROMPT_TEMPLATE = """\
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
  "simulated_action_note": "≤100字的模拟执行描述，如'假设今日买入黄金ETF（GLD），持有至2027年底，观察相对美元的实际价值变化，若黄金价格在此期间相对美元涨幅超过通胀率则视为建议奏效'",
  "monitoring_source_org": "监控机构名称（Tier1优先）",
  "monitoring_source_url": "监控数据 URL（可确定时填写，否则null）",
  "monitoring_period_note": "人读的监控时段说明",
  "monitoring_start": "监控起点 ISO 8601 日期（yyyy-mm-dd）",
  "monitoring_end": "监控终点 ISO 8601 日期（yyyy-mm-dd）",
  "reason": "一句话说明监控方案选择理由"
}}
```

若该建议完全无法通过权威数据验证效果，simulated_action_note 仍须填写，
monitoring_source_org 填"无法通过权威数据验证"，其余监控字段填 null。
"""


class SolutionSimulator:
    """为解决方案模拟执行并配置监控（Layer3 Step 4b）。"""

    async def simulate(self, solution: Solution, session: AsyncSession) -> None:
        """分析解决方案，写入 simulated_action_note 和监控字段。"""

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

        prompt = _PROMPT_TEMPLATE.format(
            claim=solution.claim,
            action_type=solution.action_type or "（未指定）",
            action_target=solution.action_target or "（未指定）",
            action_rationale=solution.action_rationale or "（未说明）",
            conclusions_section=conclusions_section,
        )

        resp = await chat_completion(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[SolutionSimulator] LLM call failed for solution id={solution.id}")
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[SolutionSimulator] Parse failed for solution id={solution.id}")
            return

        solution.simulated_action_note = parsed.get("simulated_action_note")
        solution.monitoring_source_org = parsed.get("monitoring_source_org")
        solution.monitoring_source_url = parsed.get("monitoring_source_url")
        solution.monitoring_period_note = parsed.get("monitoring_period_note")
        solution.monitoring_start = _parse_date(parsed.get("monitoring_start"))
        solution.monitoring_end = _parse_date(parsed.get("monitoring_end"))
        session.add(solution)
        await session.flush()

        logger.info(
            f"[SolutionSimulator] solution id={solution.id} → "
            f"simulated={solution.simulated_action_note and solution.simulated_action_note[:40]} | "
            f"org={solution.monitoring_source_org}"
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
