"""
Layer3 Step 1b — 隐含条件识别、共识判定与趋势分析
====================================================
对已提取的 Fact 和 Conclusion，识别其成立所必需但未明说的前提假设（隐含条件），
并经三阶段处理完成判定：

  Phase A — 识别
    调用 LLM，给出 0-2 个关键隐含前提条件，同时给出初步共识判断。
    若 LLM 在识别阶段即判定为共识 → 直接标记，跳过 Phase B。

  Phase B — 共识投票（针对 Phase A 标记为非共识或不确定的条件）
    以 3 种视角各调用 LLM 一次，每次只问：「这点是否是共识？」
    2票及以上认为是共识 → verification_result = "consensus"
    否则              → verification_result = "not_consensus"

  Phase C — 近年趋势分析
    无论 Phase B 结论如何，分析该共识近 3-5 年是在增强还是松动。
    优先使用 Tavily 联网搜索获取最新证据；无 Key 时降级为纯 LLM 判断。
    输出：consensus_trend ∈ {strengthening, weakening, stable, unknown}

结果写入：ImplicitCondition 表
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Conclusion, Fact, ImplicitCondition
from anchor.tracker.web_searcher import format_search_results, web_search

_MAX_TOKENS_IDENTIFY = 512
_MAX_TOKENS_VOTE     = 200
_MAX_TOKENS_TREND    = 300

# ---------------------------------------------------------------------------
# Phase A — 识别提示
# ---------------------------------------------------------------------------

_IDENTIFY_SYSTEM = """\
你是一名逻辑分析专家，专门识别论证中未被明说的隐含前提条件。

判断标准：
- 隐含条件必须是陈述成立的必要前提（去掉后陈述不再成立）
- 隐含条件必须在原文中未被明确陈述
- 数量：0-2个（若陈述已完全自洽则输出空数组）

初步共识标记（is_consensus）：
- true  = 该隐含条件几乎无争议地被各领域接受，如"通货膨胀会侵蚀货币购买力"
- false = 该条件存在合理争议，或依赖特定时期、模型、假设

输出必须是合法 JSON 数组，若无隐含条件则输出 []，不加其他文字。\
"""

_IDENTIFY_PROMPT = """\
## 待分析的{parent_type}陈述

{claim}

## 任务

请识别该陈述成立所必需的隐含前提条件（0-2个），并初步标记是否为普遍共识。

严格输出 JSON 数组：

```json
[
  {{
    "condition_text": "隐含条件陈述（≤60字，明确具体）",
    "is_consensus": true,
    "note": "初步判断依据（≤30字）"
  }}
]
```\
"""

# ---------------------------------------------------------------------------
# Phase B — 共识投票提示（三种视角）
# ---------------------------------------------------------------------------

_VOTE_SYSTEM = """\
你是一名共识判断专家。请判断给定陈述是否属于业界/学界/社会的**普遍共识**。

普遍共识的定义：
  在相关领域中，绝大多数专家或公众无争议地将此陈述视为背景事实。
  例如（是共识）：
    - "通货膨胀会随时间侵蚀货币购买力"
    - "市场价格由供需关系决定"
    - "税收会减少投资者的实际回报"
  例如（非共识）：
    - "当前通胀将持续高于债券名义收益率"（取决于具体时期和政策）
    - "大国冲突遵循固定历史周期"（理论框架有争议）

输出必须是合法 JSON，不加其他文字。\
"""

_VOTE_PROMPT = """\
{perspective_hint}

## 待判断陈述

{condition_text}

## 背景（此陈述是以下{parent_type}的隐含前提）

{parent_claim}

## 任务

这条陈述是否属于普遍共识？

严格输出 JSON：

```json
{{
  "is_consensus": true,
  "reason": "判断依据（≤40字）"
}}
```\
"""

_VOTE_PERSPECTIVES = [
    "[视角 1：经济学/金融学学术界]\n请从主流经济学和金融理论的角度判断该陈述是否为公认的背景事实。",
    "[视角 2：历史先例与实证数据]\n请从历史上反复观察到的规律和统计数据角度判断该陈述是否为普遍共识。",
    "[视角 3：政策制定者与国际机构]\n请从央行、IMF、世界银行等机构的研究共识角度判断该陈述是否为不争的背景条件。",
]


# ---------------------------------------------------------------------------
# Phase C — 近年趋势分析提示
# ---------------------------------------------------------------------------

_TREND_SYSTEM = """\
你是一名知识动态分析专家，专门评估学术/政策/公众共识的稳定性变化趋势。

趋势定义（均相对于"成为共识"的方向）：
- strengthening（增强）：近3-5年内，该陈述获得更多研究支持、机构背书或实证验证，接受度上升
- weakening（松动）：  近3-5年内，出现重要反例、修正性研究或政策转向，接受度下降或争议加大
- stable（稳定）：     近3-5年内无显著变化，共识状态基本维持
- unknown（不确定）：  资料不足，无法识别明确趋势

输出必须是合法 JSON，不加其他文字。\
"""

_TREND_PROMPT = """\
## 待评估陈述

{condition_text}

## 背景（此陈述是以下{parent_type}的隐含前提）

{parent_claim}

## 近期参考资料

{search_context}

## 任务

请判断近3-5年（2020–2025年）内，该陈述在学术界/政策界的接受度趋势。

严格输出 JSON：

```json
{{
  "trend": "strengthening",
  "reason": "趋势判断依据（≤60字，如有新研究、机构转向或反例请简述）"
}}
```\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class ImplicitConditionExtractor:
    """识别事实和结论中的隐含条件，投票判断是否为共识，并分析近年趋势（Layer3 Step 1b）。"""

    async def extract_for_facts(
        self, facts: list[Fact], session: AsyncSession
    ) -> list[ImplicitCondition]:
        all_conditions: list[ImplicitCondition] = []
        for fact in facts:
            if not fact.claim:
                continue
            conditions = await self._process("事实", fact.claim, fact_id=fact.id, session=session)
            all_conditions.extend(conditions)
        return all_conditions

    async def extract_for_conclusions(
        self, conclusions: list[Conclusion], session: AsyncSession
    ) -> list[ImplicitCondition]:
        all_conditions: list[ImplicitCondition] = []
        for conclusion in conclusions:
            if not conclusion.claim:
                continue
            conditions = await self._process(
                "结论", conclusion.claim, conclusion_id=conclusion.id, session=session
            )
            all_conditions.extend(conditions)
        return all_conditions

    # ── 内部流程 ──────────────────────────────────────────────────────────────

    async def _process(
        self,
        parent_type: str,
        claim: str,
        session: AsyncSession,
        fact_id: Optional[int] = None,
        conclusion_id: Optional[int] = None,
    ) -> list[ImplicitCondition]:
        parent_label = f"Fact#{fact_id}" if fact_id else f"Conclusion#{conclusion_id}"

        # ── Phase A: 识别 ─────────────────────────────────────────────────────
        identified = await self._identify(parent_type, claim)
        if not identified:
            logger.debug(f"[ImplicitConditionExtractor] {parent_label} 无隐含条件")
            return []

        logger.info(
            f"[ImplicitConditionExtractor] {parent_label} 识别出 {len(identified)} 个隐含条件"
        )

        results: list[ImplicitCondition] = []
        for item in identified:
            condition_text = item.get("condition_text", "").strip()
            if not condition_text:
                continue

            phase_a_consensus = bool(item.get("is_consensus", False))
            phase_a_note      = item.get("note", "")

            ic = ImplicitCondition(
                fact_id=fact_id,
                conclusion_id=conclusion_id,
                condition_text=condition_text,
            )

            # ── Phase B: 共识投票 ─────────────────────────────────────────────
            if phase_a_consensus:
                ic.verification_result = "consensus"
                ic.verification_note   = phase_a_note or "识别阶段判定为普遍共识"
                ic.vote_consensus      = 3
                ic.vote_not_consensus  = 0
                logger.info(
                    f"[ImplicitConditionExtractor] {parent_label} "
                    f"「{condition_text[:40]}」→ consensus（Phase A 直接确认）"
                )
            else:
                vote_result = await self._vote_consensus(condition_text, claim, parent_type)
                ic.verification_result = vote_result["result"]
                ic.verification_note   = vote_result["note"]
                ic.vote_consensus      = vote_result["vote_consensus"]
                ic.vote_not_consensus  = vote_result["vote_not_consensus"]
                logger.info(
                    f"[ImplicitConditionExtractor] {parent_label} "
                    f"「{condition_text[:40]}」→ {ic.verification_result} "
                    f"(共识={ic.vote_consensus}/非共识={ic.vote_not_consensus})"
                )

            # ── Phase C: 近年趋势分析 ─────────────────────────────────────────
            trend_result = await self._analyze_trend(condition_text, claim, parent_type)
            ic.consensus_trend      = trend_result["trend"]
            ic.consensus_trend_note = trend_result["reason"]
            logger.info(
                f"[ImplicitConditionExtractor] {parent_label} "
                f"「{condition_text[:40]}」→ trend={ic.consensus_trend}"
            )

            session.add(ic)
            await session.flush()
            results.append(ic)

        return results

    async def _identify(self, parent_type: str, claim: str) -> list[dict]:
        """Phase A：调用 LLM 识别隐含条件。"""
        prompt = _IDENTIFY_PROMPT.format(parent_type=parent_type, claim=claim)
        resp = await chat_completion(
            system=_IDENTIFY_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_IDENTIFY,
        )
        if resp is None:
            return []
        return _parse_json_array(resp.content)

    async def _vote_consensus(
        self, condition_text: str, parent_claim: str, parent_type: str
    ) -> dict:
        """Phase B：3视角投票，只问「这条隐含条件是否是普遍共识」。"""
        vote_consensus     = 0
        vote_not_consensus = 0
        notes: list[str]   = []

        for perspective in _VOTE_PERSPECTIVES:
            prompt = _VOTE_PROMPT.format(
                perspective_hint=perspective,
                condition_text=condition_text,
                parent_type=parent_type,
                parent_claim=parent_claim,
            )
            resp = await chat_completion(
                system=_VOTE_SYSTEM,
                user=prompt,
                max_tokens=_MAX_TOKENS_VOTE,
            )
            if not resp:
                continue
            parsed = _parse_json_obj(resp.content)
            if not parsed:
                continue

            if parsed.get("is_consensus", False):
                vote_consensus += 1
            else:
                vote_not_consensus += 1

            if parsed.get("reason"):
                notes.append(parsed["reason"])

        is_consensus = vote_consensus >= 2
        result = "consensus" if is_consensus else "not_consensus"

        if len(notes) >= 2 and vote_consensus == vote_not_consensus:
            note = f"[同票，判定非共识] {notes[0]}"
        elif notes:
            prefix = f"[共识 {vote_consensus}/3票] " if is_consensus else f"[非共识 {vote_not_consensus}/3票] "
            note = prefix + notes[0]
        else:
            note = "LLM验证失败"

        return {
            "result":             result,
            "note":               note[:120],
            "vote_consensus":     vote_consensus,
            "vote_not_consensus": vote_not_consensus,
        }

    async def _analyze_trend(
        self, condition_text: str, parent_claim: str, parent_type: str
    ) -> dict:
        """Phase C：分析近3-5年共识趋势，优先联网搜索，降级为纯 LLM。"""
        # 构建搜索查询
        query = _build_trend_query(condition_text)
        search_results = await web_search(query, max_results=4)

        if search_results:
            search_context = format_search_results(search_results)
            logger.debug(
                f"[ImplicitConditionExtractor] 趋势分析搜索到 {len(search_results)} 条结果: {query[:60]}"
            )
        else:
            search_context = "（无联网搜索结果，依据训练知识判断）"

        prompt = _TREND_PROMPT.format(
            condition_text=condition_text,
            parent_type=parent_type,
            parent_claim=parent_claim,
            search_context=search_context,
        )
        resp = await chat_completion(
            system=_TREND_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS_TREND,
        )
        if not resp:
            return {"trend": "unknown", "reason": "LLM调用失败"}

        parsed = _parse_json_obj(resp.content)
        if not parsed:
            return {"trend": "unknown", "reason": "解析失败"}

        trend = parsed.get("trend", "unknown")
        if trend not in ("strengthening", "weakening", "stable", "unknown"):
            trend = "unknown"

        return {
            "trend":  trend,
            "reason": parsed.get("reason", "")[:100],
        }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_trend_query(condition_text: str) -> str:
    """构建用于搜索近年趋势的查询字符串。"""
    base = condition_text[:80]
    return f"{base} consensus research trend 2023 2024 2025"


def _parse_json_array(raw: str) -> list[dict]:
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("[")
        end   = json_str.rfind("]") + 1
        if start == -1 or end == 0:
            return []
        json_str = json_str[start:end]
    try:
        result = json.loads(json_str)
        return result if isinstance(result, list) else []
    except Exception as exc:
        logger.warning(f"[ImplicitConditionExtractor] JSON array parse error: {exc}\nRaw: {raw[:200]}")
        return []


def _parse_json_obj(raw: str) -> dict | None:
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
        logger.warning(f"[ImplicitConditionExtractor] JSON obj parse error: {exc}\nRaw: {raw[:200]}")
        return None
