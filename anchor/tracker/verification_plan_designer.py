"""
验证方案设计器 — 多模型交叉验证（Layer3 Step 1 前置）
=====================================================
三阶段流程：

  1. 多模型独立提案
     每个配置的 LLM 独立给出 3 套验证策略（使用不同数据源）。
     若只配置一个模型，则调用 3 次（依赖模型随机性获取多样方案）。

  2. 语义归一化
     用主 LLM 将内容相同但表述不同的策略合并，避免因表述差异导致"一票方案"。

  3. 投票选优
     选出归一化后得票最高的策略作为最终验证方案。

每套方案包含：
  - data_source   — 应查询的权威机构名 + 数据集
  - search_query  — 用于 Tavily 搜索的精确查询词
  - metric        — 应对比的具体指标
  - threshold     — 判定为 true/false 的量化阈值
  - evidence_tier — 预期证据等级（1/2/3）

配置方式（.env）：
  VERIFICATION_PLAN_MODELS=claude-opus-4-6,claude-sonnet-4-6,claude-haiku-4-5-20251001
  # 不填则使用主 llm_model 调用 3 次
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from anchor.llm_client import chat_completion
from anchor.models import Fact, VerificationReference


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class VerificationPlan:
    """多模型投票选出的最优验证方案"""

    data_source: str          # 数据来源机构（如"美国劳工统计局 BLS"）
    dataset: str              # 具体数据集/报告名
    search_query: str         # 搜索查询词（用于 Tavily）
    metric: str               # 需要对比的具体指标
    threshold: str            # 判定阈值
    evidence_tier: Optional[int]  # 预期证据等级（1/2/3）
    vote_count: int = 1       # 归一化后该方案获得的票数

    def to_dict(self) -> dict:
        return {
            "data_source": self.data_source,
            "dataset": self.dataset,
            "search_query": self.search_query,
            "metric": self.metric,
            "threshold": self.threshold,
            "evidence_tier": self.evidence_tier,
            "vote_count": self.vote_count,
        }


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """\
你是一名专业的数据核查策略师。给定一条事实陈述，请设计 3 套不同的验证方案。

每套方案需要：
- 明确指出应查询哪个权威数据源（机构名 + 具体数据集）
- 给出可直接用于搜索的精确查询词（英文优先，≤20个词）
- 说明应对比的具体指标和判定阈值

要求：
- 3 套方案必须使用不同的数据来源机构（不得重复）
- 优先选择 Tier1 数据源（政府/监管机构/央行/国际组织/上市公司财报）
- 每套方案必须是可操作的，即存在真实可查的数据集
- 若提取阶段提供了引用来源，可将其作为方案之一（仍需另外两套独立方案）
- 不得依赖媒体评论、主观评级或无法独立核实的数据

**跨年期聚合策略（重要）：**
若事实陈述涉及多年总量、累计增量或长期趋势（如"过去20年"、"近十年"、"2000-2020年累计"），
数据源通常只发布年度数据，此时应：
- metric 字段明确写出聚合方式，例如"2000-2020年各年度银行贷款增量之和"或"近20年年均GDP增速"
- search_query 搜索各年度数据或年度序列，如"Fed H.8 bank loans annual 2000-2020"
- threshold 说明聚合后如何判断（如"年度数据相加得出20年总增量，与陈述数字对比"）
- 不要寻找一个不存在的"多年综合上限定义"，应拆解为年度数据的汇总

证据等级：
- Tier 1：政府统计局、央行声明、法院判决、上市公司官方财报
- Tier 2：主要交易所官方价格、股票/债券/商品指数
- Tier 3：引用了 Tier1/2 数据的可信媒体报道

输出必须是合法 JSON，不含其他文字。\
"""

_PLAN_USER_TEMPLATE = """\
## 待验证事实

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实时间范围：{validity_start} 至 {validity_end}
{references_section}
## 任务

为上述事实设计 3 套使用不同权威数据源的验证方案。

严格输出 JSON：

```json
{{
  "plans": [
    {{
      "plan_id": "P1",
      "data_source": "机构名（如'美国劳工统计局 BLS'）",
      "dataset": "具体数据集/报告名（如'就业形势摘要 Employment Situation Summary'）",
      "search_query": "用于搜索的精确查询词，英文优先，≤20词",
      "metric": "需要对比的具体指标（如'非农就业新增人数'）",
      "threshold": "判定 true/false 的量化阈值（如'新增≥100K即为 true'）",
      "evidence_tier": 1
    }},
    {{
      "plan_id": "P2",
      "data_source": "...",
      "dataset": "...",
      "search_query": "...",
      "metric": "...",
      "threshold": "...",
      "evidence_tier": 2
    }},
    {{
      "plan_id": "P3",
      "data_source": "...",
      "dataset": "...",
      "search_query": "...",
      "metric": "...",
      "threshold": "...",
      "evidence_tier": 3
    }}
  ]
}}
```\
"""

# 同一模型多次调用时，分配不同视角提示以提升方案多样性
_PERSPECTIVE_HINTS = [
    (
        "【视角限定】本次请优先考虑 **政府/监管机构/央行** 的官方一手数据来源"
        "（如国家统计局、美联储、SEC、港交所披露易等），将最强的 Tier1 方案放在 P1。"
    ),
    (
        "【视角限定】本次请优先考虑 **金融市场价格/交易所/指数** 数据来源"
        "（如 CME 官方价格、标普道琼斯指数、彭博指数等 Tier2 数据），将市场数据方案放在 P1。"
    ),
    (
        "【视角限定】本次请优先考虑 **国际组织/跨境机构/可信第三方聚合数据**"
        "（如 IMF、世界银行、BIS、OECD 等），将国际机构方案放在 P1。"
    ),
]

_NORMALIZE_SYSTEM = """\
你是一名数据分析专家，擅长识别语义相同但表述不同的内容。

给定多个验证方案提案，请将语义相同或非常接近的方案归为一组。

判断"相同"的标准（满足以下全部）：
- 使用同一个数据来源机构（无论中英文表述）
- 查询同一类数据集或报告
- 对比同一个核心指标

（细微措辞差异、语言差异、阈值表述差异不影响相同性判断）

为每组选出最清晰完整的代表性描述，统计票数。
按 vote_count 降序排列。

输出必须是合法 JSON，不含其他文字。\
"""

_NORMALIZE_USER_TEMPLATE = """\
以下是 {n} 条验证方案提案（来自 {n_models} 个 LLM 模型，针对同一事实）：

{proposals_text}

请将语义相同的方案归组，统计每组票数，选出每组最佳代表描述。

严格输出 JSON：

```json
{{
  "groups": [
    {{
      "data_source": "归一化后的机构名",
      "dataset": "归一化后的数据集名",
      "search_query": "最佳搜索查询词",
      "metric": "指标",
      "threshold": "判定阈值",
      "evidence_tier": 1,
      "vote_count": 3
    }}
  ]
}}
```

按 vote_count 降序排列。\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class VerificationPlanDesigner:
    """为事实验证设计最优方案（多模型交叉验证）。"""

    async def design(
        self,
        fact: Fact,
        references: list[VerificationReference],
    ) -> VerificationPlan | None:
        """为事实生成最优验证方案。

        Args:
            fact: 待验证的事实
            references: 提取阶段建议的权威来源（来自 VerificationReference 表）

        Returns:
            得票最高的验证方案；所有 LLM 调用失败时返回 None。
        """
        from anchor.config import settings

        model_list = settings.verification_plan_model_list

        # 当同一模型出现多次时，分配不同视角提示以提升方案多样性
        perspective_hints = _assign_perspective_hints(model_list)

        # Phase 1: 多模型独立提案（并发）
        tasks = [
            self._get_proposals(fact, references, model_id, hint)
            for model_id, hint in zip(model_list, perspective_hints)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_proposals: list[dict] = []
        for r in results:
            if isinstance(r, list):
                all_proposals.extend(r)

        if not all_proposals:
            logger.warning(
                f"[PlanDesigner] fact id={fact.id}: 所有 {len(model_list)} 个模型均未返回方案"
            )
            return None

        logger.info(
            f"[PlanDesigner] fact id={fact.id}: 收集到 {len(all_proposals)} 条方案"
            f"（来自 {len(model_list)} 个模型）"
        )

        # 只有 1 条提案时直接使用，跳过归一化
        if len(all_proposals) == 1:
            return _proposal_to_plan(
                all_proposals[0],
                fallback_query=fact.verifiable_expression or fact.claim[:60],
                vote_count=1,
            )

        # Phase 2: 归一化 + 投票
        return await self._normalize_and_vote(fact, all_proposals, len(model_list))

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    async def _get_proposals(
        self,
        fact: Fact,
        references: list[VerificationReference],
        model_id: str,
        perspective_hint: str = "",
    ) -> list[dict]:
        """调用单个模型，获取其给出的 3 套验证方案。

        perspective_hint: 当同一模型被调用多次时注入的视角提示，空字符串表示无限定。
        """
        references_section = ""
        if references:
            refs_text = "\n".join(
                f"  - {r.organization}：{r.data_description}"
                + (f"（URL: {r.url}）" if r.url else "")
                for r in references
            )
            references_section = (
                f"\n## 提取阶段建议的参考来源（可选用其中一套作为方案）\n{refs_text}\n"
            )

        # 若有视角提示则追加到 prompt 末尾（在 JSON 模板之前）
        perspective_section = f"\n{perspective_hint}\n" if perspective_hint else ""

        prompt = _PLAN_USER_TEMPLATE.format(
            claim=fact.claim,
            verifiable_expression=fact.verifiable_expression or "（未提供）",
            validity_start=fact.validity_start_note or "不限",
            validity_end=fact.validity_end_note or "不限",
            references_section=references_section,
        ) + perspective_section

        resp = await chat_completion(
            system=_PLAN_SYSTEM,
            user=prompt,
            max_tokens=1024,
            model=model_id,
        )
        if resp is None:
            logger.warning(f"[PlanDesigner] model={model_id!r} 调用失败")
            return []

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[PlanDesigner] model={model_id!r} JSON 解析失败")
            return []

        plans = parsed.get("plans", [])
        if not isinstance(plans, list):
            return []

        valid = [p for p in plans if isinstance(p, dict) and p.get("data_source")]
        logger.debug(f"[PlanDesigner] model={model_id!r} → {len(valid)} 条有效方案")
        return valid

    async def _normalize_and_vote(
        self,
        fact: Fact,
        proposals: list[dict],
        n_models: int,
    ) -> VerificationPlan | None:
        """归一化方案并投票，返回获胜方案。"""
        proposals_text = "\n".join(
            f"{i + 1}. 机构={p.get('data_source', '')} | "
            f"数据集={p.get('dataset', '')} | "
            f"指标={p.get('metric', '')} | "
            f"查询词={p.get('search_query', '')}"
            for i, p in enumerate(proposals)
        )

        prompt = _NORMALIZE_USER_TEMPLATE.format(
            n=len(proposals),
            n_models=n_models,
            proposals_text=proposals_text,
        )

        resp = await chat_completion(
            system=_NORMALIZE_SYSTEM,
            user=prompt,
            max_tokens=1024,
        )

        fallback = proposals[0]
        fallback_query = fact.verifiable_expression or fact.claim[:60]

        if resp is None:
            logger.warning(
                f"[PlanDesigner] fact id={fact.id} 归一化 LLM 失败，使用第一个方案"
            )
            return _proposal_to_plan(fallback, fallback_query, vote_count=1)

        parsed = _parse_json(resp.content)
        if not parsed:
            return _proposal_to_plan(fallback, fallback_query, vote_count=1)

        groups = parsed.get("groups") or []
        if not groups:
            return _proposal_to_plan(fallback, fallback_query, vote_count=1)

        groups_sorted = sorted(
            groups, key=lambda g: g.get("vote_count", 0), reverse=True
        )
        winner = groups_sorted[0]
        plan = VerificationPlan(
            data_source=winner.get("data_source", ""),
            dataset=winner.get("dataset", ""),
            search_query=winner.get("search_query") or fallback_query,
            metric=winner.get("metric", ""),
            threshold=winner.get("threshold", ""),
            evidence_tier=_parse_tier(winner.get("evidence_tier")),
            vote_count=winner.get("vote_count", 1),
        )

        logger.info(
            f"[PlanDesigner] fact id={fact.id} 获胜方案: "
            f"{plan.data_source!r} | 票数={plan.vote_count}/{len(proposals)}"
        )
        return plan


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _assign_perspective_hints(model_list: list[str]) -> list[str]:
    """为每个模型调用分配视角提示。

    规则：
    - 若 model_list 中存在重复模型（同一模型被调用多次），
      则按 _PERSPECTIVE_HINTS 循环分配不同视角，确保每次调用侧重不同数据来源。
    - 若所有模型均不同（真正的多模型配置），不注入视角提示（各模型自主发挥）。
    """
    if len(set(model_list)) < len(model_list):
        # 存在重复：按顺序分配视角提示
        return [
            _PERSPECTIVE_HINTS[i % len(_PERSPECTIVE_HINTS)]
            for i in range(len(model_list))
        ]
    # 所有模型不同：无需额外提示
    return [""] * len(model_list)


def _proposal_to_plan(
    p: dict,
    fallback_query: str,
    vote_count: int,
) -> VerificationPlan:
    return VerificationPlan(
        data_source=p.get("data_source", ""),
        dataset=p.get("dataset", ""),
        search_query=p.get("search_query") or fallback_query,
        metric=p.get("metric", ""),
        threshold=p.get("threshold", ""),
        evidence_tier=_parse_tier(p.get("evidence_tier")),
        vote_count=vote_count,
    )


def _parse_tier(value) -> int | None:
    if value in (1, 2, 3):
        return value
    if isinstance(value, str) and value.strip() in ("1", "2", "3"):
        return int(value.strip())
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
        logger.warning(f"[PlanDesigner] JSON parse error: {exc}")
        return None
