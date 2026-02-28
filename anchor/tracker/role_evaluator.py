"""
Layer3 Step 7 — 角色匹配评估器
================================
在裁定推导完成后，评估作者角色与其观点/建议是否匹配：
  "这个人说这句话，在他的角色和专业背景下，是否合适？"

role_fit 三档（宽松标准）：
  appropriate  — 作者有相关背景，且观点基于合理论据（不要求严格局限于核心领域）
  questionable — 作者与该领域缺乏明确关联，且观点依赖纯粹的领域专业知识才能成立
  mismatched   — 作者背景与观点领域完全无关（如厨师发表货币政策断言）

核心原则：
  - 只要作者有部分相关背景，且论证基于事实/数据/历史（而非纯主观断言），应倾向 appropriate
  - questionable 用于：作者与该领域无任何明显关联，且观点依赖高度专业的领域知识
  - mismatched 仅用于极端情况，保持罕见
  - 高可信度作者（Tier 1-2）：只要观点在其研究范围内有任何交集，默认 appropriate
  - 不因"超出核心专业"而降级，除非是跨度极大的领域跨越

注意：此评估不质疑观点对错（那是 ConditionVerifier 的任务），只评估"说话资格"。

结果写入：
  ConclusionVerdict.role_fit / role_fit_note
  SolutionAssessment.role_fit / role_fit_note
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Author,
    Conclusion,
    ConclusionVerdict,
    Solution,
    SolutionAssessment,
)

_MAX_TOKENS = 512

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SYSTEM = """\
你是一名媒体信源分析专家。你的任务是评估：某位特定背景的人发表某类观点，\
是否与其职业角色和专业领域相匹配。

这不是评估观点是否正确，而是评估"说话资格"——此人是否有合理的立场来发表此类判断。

**连带领域原则（重要）：**
以下学科在实践中高度交织，应视为同一专业生态圈，跨领域发言无需降级：
  - 宏观经济 ↔ 国际关系 ↔ 地缘政治 ↔ 财政/货币政策 ↔ 金融市场
  - 历史研究 ↔ 政治经济学 ↔ 战略研究
  - 投资/资产配置 ↔ 风险分析 ↔ 宏观预测
  例：宏观经济学家讨论大国博弈、战争风险、政权稳定——这是标准实践，不是越界。

**宽松评估原则：**
只要满足以下任意一条，即判 appropriate：
  - 观点落在作者专业领域或上述连带领域内
  - 观点基于作者长期研究的历史/经济/市场数据得出
  - 作者为 Tier 1–2，且与该话题有任何研究或实践交集

questionable 的判断门槛（需同时满足）：
  - 观点严重依赖某一与上述连带领域完全无关的专业技术知识
  - 作者在该专业方向没有任何明显背景

mismatched 仅用于极端情况（保持罕见）：
  - 作者背景与话题领域毫无交集（如厨师发表军事技术断言）

**不要因为"话题超出核心专业"就降级**，关键是作者是否有合理基础做此判断。

输出必须是合法 JSON，不加任何其他文字。\
"""

# ---------------------------------------------------------------------------
# 用户提示
# ---------------------------------------------------------------------------

_PROMPT = """\
## 作者档案
姓名：{name}
职业角色：{role}
专业领域：{expertise_areas}
已知立场偏见：{known_biases}
可信度分级：Tier {credibility_tier}（{tier_label}）
综合描述：{profile_note}

## 待评估观点
类型：{claim_type}
核心陈述：{claim}

## 评估任务
判断此人发表上述观点的角色匹配度。

评估思路（按顺序检查，满足即 appropriate）：
1. 观点是否在作者专业领域或相邻领域内？→ appropriate
2. 作者是否基于其长期研究的历史/经济/市场数据得出此判断？→ appropriate
3. Tier 1–2 作者：与该话题是否有任何研究或实践交集？→ appropriate
4. 以上均否，且观点严重依赖作者完全陌生的专业技术？→ questionable
5. 作者背景与话题领域完全无关？→ mismatched（保持罕见）

**默认偏向 appropriate，除非有明确理由降级。**

严格输出 JSON：

```json
{{
  "role_fit": "<appropriate|questionable|mismatched>",
  "role_fit_note": "<1句话分析，≤60字，说明为何匹配或为何存在不匹配>"
}}
```\
"""

_TIER_LABELS = {
    1: "顶级权威",
    2: "行业专家",
    3: "知名评论员",
    4: "普通媒体/KOL",
    5: "未知",
}


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class RoleEvaluator:
    """评估作者角色与观点的匹配度（Layer3 Step 7）。"""

    async def evaluate_conclusion_verdict(
        self,
        verdict: ConclusionVerdict,
        conclusion: Conclusion,
        author: Author,
        session: AsyncSession,
    ) -> None:
        """为 ConclusionVerdict 填写 role_fit / role_fit_note。"""
        if verdict.role_fit is not None:
            logger.debug(f"[RoleEvaluator] verdict id={verdict.id} already evaluated, skip")
            return

        role_fit, role_fit_note = await self._evaluate(
            author=author,
            claim=conclusion.claim,
            claim_type="结论（" + conclusion.conclusion_type + "）",
        )
        verdict.role_fit = role_fit
        verdict.role_fit_note = role_fit_note
        session.add(verdict)
        await session.flush()

        logger.info(
            f"[RoleEvaluator] conclusion_verdict id={verdict.id} | "
            f"role_fit={role_fit} | {role_fit_note}"
        )

    async def evaluate_solution_assessment(
        self,
        assessment: SolutionAssessment,
        solution: Solution,
        author: Author,
        session: AsyncSession,
    ) -> None:
        """为 SolutionAssessment 填写 role_fit / role_fit_note。"""
        if assessment.role_fit is not None:
            logger.debug(f"[RoleEvaluator] assessment id={assessment.id} already evaluated, skip")
            return

        claim = solution.claim
        if solution.action_type and solution.action_target:
            claim = f"{solution.claim}（{solution.action_type} {solution.action_target}）"

        role_fit, role_fit_note = await self._evaluate(
            author=author,
            claim=claim,
            claim_type="行动建议",
        )
        assessment.role_fit = role_fit
        assessment.role_fit_note = role_fit_note
        session.add(assessment)
        await session.flush()

        logger.info(
            f"[RoleEvaluator] solution_assessment id={assessment.id} | "
            f"role_fit={role_fit} | {role_fit_note}"
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _evaluate(
        self,
        author: Author,
        claim: str,
        claim_type: str,
    ) -> tuple[str, str | None]:
        """调用 LLM 评估角色匹配度，返回 (role_fit, role_fit_note)。"""

        tier = author.credibility_tier or 5
        prompt = _PROMPT.format(
            name=author.name,
            role=author.role or "未知",
            expertise_areas=author.expertise_areas or "未知",
            known_biases=author.known_biases or "无",
            credibility_tier=tier,
            tier_label=_TIER_LABELS.get(tier, "未知"),
            profile_note=author.profile_note or "无",
            claim_type=claim_type,
            claim=claim,
        )

        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[RoleEvaluator] LLM call failed for claim: {claim[:50]}")
            return "questionable", None

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[RoleEvaluator] JSON parse failed for claim: {claim[:50]}")
            return "questionable", None

        role_fit = parsed.get("role_fit", "questionable")
        if role_fit not in ("appropriate", "questionable", "mismatched"):
            role_fit = "questionable"

        role_fit_note = parsed.get("role_fit_note") or None
        return role_fit, role_fit_note


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


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
        logger.warning(f"[RoleEvaluator] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
