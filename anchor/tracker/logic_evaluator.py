"""
Layer3 Step 2+3 — 逻辑评估器
==============================
在事实验证（Step 1）完成后执行，对每条 Logic 进行：

  Step 2: 逻辑完备性评估
    结合已验证事实的状态，判断"事实→结论/预测"的论证是否严密。

  Step 3: 一句话总结
    输出极简摘要（≤30字），描述这条逻辑的核心论证。

两步合并为一次 LLM 调用，结果写入 Logic 的对应字段：
  - logic_completeness
  - logic_note
  - one_sentence_summary
  - assessed_at
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Conclusion,
    Fact,
    FactEvaluation,
    Logic,
    LogicCompleteness,
    Solution,
    _utcnow,
)

_MAX_TOKENS = 768

_SYSTEM_PROMPT = """\
你是一名逻辑分析专家。给定一条论证关系（包含支撑事实、假设条件和目标结论/预测），
以及这些事实的验证状态，请评估论证的完备性并生成一句话总结。

输出必须是合法的 JSON，不加任何其他文字。
"""

_PROMPT_TEMPLATE = """\
## 论证目标

类型：{target_type}
核心陈述：{target_claim}

## 支撑事实（已知证据）

{supporting_section}

## 假设条件（待验证前提）

{assumption_section}

## 任务

基于以上信息，完成：

1. **逻辑完备性评估**：从支撑事实到目标结论/预测的推理是否严密？
2. **一句话总结**：用自己的话抽象这条论证在表达什么核心观点，不评判对错，只描述作者的论证逻辑

严格输出 JSON：

```json
{{
  "logic_completeness": "<complete|partial|weak|invalid>",
  "logic_note": "<1句逻辑分析说明，指出具体缺陷或亮点>",
  "one_sentence_summary": "<≤30字，抽象描述这条论证的核心观点，不评判，不说'未证明'或'缺乏'，只说这个论证在主张什么>"
}}
```

logic_completeness 说明：
  complete = 从事实到结论/预测逻辑链条完整，无明显跳步
  partial  = 逻辑有一定支撑，但存在跳步或隐含假设
  weak     = 事实与结论/预测关联性低，论证牵强
  invalid  = 存在明显逻辑谬误（循环论证、以偏概全等）
"""


class LogicEvaluator:
    """评估 Logic 的完备性并生成一句话总结（Layer3 Step 2+3）。"""

    async def evaluate(self, logic: Logic, session: AsyncSession) -> None:
        """执行逻辑评估，将结果写入 logic 对象（session.add 由调用方负责 commit）。"""

        # ── 解析 Fact ID 列表 ────────────────────────────────────────────────
        supporting_ids = json.loads(logic.supporting_fact_ids or "[]")
        assumption_ids = json.loads(logic.assumption_fact_ids or "[]")
        all_fact_ids = list(set(supporting_ids + assumption_ids))

        # ── 加载事实内容和最新评估状态 ───────────────────────────────────────
        fact_map: dict[int, Fact] = {}
        if all_fact_ids:
            fact_result = await session.exec(
                select(Fact).where(Fact.id.in_(all_fact_ids))
            )
            for f in fact_result.all():
                fact_map[f.id] = f

        eval_map = await _load_latest_evaluations(session, all_fact_ids)

        # ── 加载目标结论/解决方案 ────────────────────────────────────────────
        target_claim = "（未找到）"
        target_type = "conclusion" if logic.logic_type == "inference" else "solution"
        if logic.conclusion_id:
            c_result = await session.exec(
                select(Conclusion).where(Conclusion.id == logic.conclusion_id)
            )
            conc = c_result.first()
            if conc:
                target_claim = conc.claim
        elif logic.solution_id:
            s_result = await session.exec(
                select(Solution).where(Solution.id == logic.solution_id)
            )
            sol = s_result.first()
            if sol:
                target_claim = sol.claim

        # ── 构建 prompt ───────────────────────────────────────────────────────
        supporting_section = _format_facts(supporting_ids, fact_map, eval_map) or "（无）"
        assumption_section = _format_facts(assumption_ids, fact_map, eval_map) or "（无）"

        prompt = _PROMPT_TEMPLATE.format(
            target_type="结论" if target_type == "conclusion" else "解决方案",
            target_claim=target_claim,
            supporting_section=supporting_section,
            assumption_section=assumption_section,
        )

        # ── 调用 LLM ──────────────────────────────────────────────────────────
        resp = await chat_completion(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[LogicEvaluator] LLM call failed for logic id={logic.id}")
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[LogicEvaluator] Parse failed for logic id={logic.id}")
            return

        # ── 写入 Logic 字段 ───────────────────────────────────────────────────
        try:
            lc_val = parsed.get("logic_completeness")
            if lc_val:
                logic.logic_completeness = LogicCompleteness(lc_val)
        except ValueError:
            pass

        logic.logic_note = parsed.get("logic_note")
        logic.one_sentence_summary = parsed.get("one_sentence_summary")
        logic.assessed_at = _utcnow()
        session.add(logic)
        await session.flush()

        logger.info(
            f"[LogicEvaluator] logic id={logic.id} → "
            f"completeness={logic.logic_completeness} | "
            f"summary={logic.one_sentence_summary}"
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _format_facts(
    fact_ids: list[int],
    fact_map: dict[int, Fact],
    eval_map: dict[int, str],
) -> str:
    if not fact_ids:
        return ""
    lines = []
    for fid in fact_ids:
        f = fact_map.get(fid)
        if f is None:
            lines.append(f"  - [Fact #{fid}] 未找到")
            continue
        ev = eval_map.get(fid, "未验证")
        lines.append(f"  - [Fact #{fid}] 验证状态={ev}")
        lines.append(f"    事实: {f.claim}")
        if f.verifiable_expression:
            lines.append(f"    可验证表达: {f.verifiable_expression}")
        if f.verified_source_org:
            lines.append(f"    核查来源: {f.verified_source_org}")
    return "\n".join(lines)


async def _load_latest_evaluations(
    session: AsyncSession, fact_ids: list[int]
) -> dict[int, str]:
    """返回 {fact_id: latest_result_str}"""
    if not fact_ids:
        return {}
    result = await session.exec(
        select(FactEvaluation)
        .where(FactEvaluation.fact_id.in_(fact_ids))
        .order_by(FactEvaluation.evaluated_at.desc())
    )
    latest: dict[int, str] = {}
    for ev in result.all():
        if ev.fact_id not in latest:
            latest[ev.fact_id] = ev.result.value
    return latest


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
        logger.warning(f"[LogicEvaluator] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
