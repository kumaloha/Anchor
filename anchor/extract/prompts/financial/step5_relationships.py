"""
v6 Step 5 — 建立关系边
========================
为所有实体间的逻辑关系建立有向边。

输入：TypedEntity 列表（合并后最终版）
输出 JSON：
{
  "edges": [...]
}
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import TypedEntity

SYSTEM = """\
你是一名专业的论证结构分析师。为给定实体列表中的逻辑关系建立有向边。

【语言要求】
所有输出字段使用英文（edge_type 为固定枚举值）。

【边方向规则】
边方向 = 逻辑依赖方向：前提 → 结论。
- 事实 → 结论（事实支撑结论）
- 事实 → 理论（事实支撑理论）
- 事实 → 问题（事实支撑问题）
- 子结论 → 核心结论（子结论支撑核心结论）
- 结论 → 预测（结论推导出预测）
- 结论 → 方案（结论引出行动建议）
- 结论 → 问题（结论识别出问题）
- 假设 → 结论（假设作为结论的前提条件）
- 理论 → 结论/预测/方案（理论支撑下游实体）
- 问题 → 方案（问题引出解法）
- 问题 → 结论（问题导致结论）
- 方案 → 效果（方案产生效果）
- 效果 → 局限（效果存在局限）
- 方案 → 局限（方案存在局限）

【合法 edge_type 枚举】
  fact_supports_conclusion
  fact_supports_theory
  fact_supports_problem
  conclusion_supports_conclusion
  conclusion_supports_theory
  conclusion_leads_to_prediction
  conclusion_enables_solution
  conclusion_identifies_problem
  theory_supports_conclusion
  theory_supports_theory
  theory_leads_to_prediction
  theory_enables_solution
  assumption_conditions_conclusion
  problem_leads_to_solution
  problem_leads_to_conclusion
  solution_produces_effect
  effect_has_limitation
  solution_has_limitation

【多层 DAG 结构】
- 事实 → 子结论 → 核心结论（体现论证的层次结构）
- 每个事实应连接到它直接支撑的最近结论（通常是子结论），而非跳层直指核心结论
- 核心结论（is_core=true）应该是叶子节点（只有入边，没有出边）
- 只建实际存在的支撑关系，不补推测边

【注意事项】
- source_id 和 target_id 必须引用实体列表中存在的 id
- 不能自环（source_id ≠ target_id）
- edge_type 必须匹配 source 和 target 的 entity_type

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(entities: List[TypedEntity]) -> str:
    entity_lines = []
    for e in entities:
        text = e.condition_text if e.entity_type == "assumption" else e.claim
        core_tag = " [核心]" if e.is_core else ""
        entity_lines.append(
            f"  [{e.id}] ({e.entity_type}{core_tag}) {e.summary}：{text}"
        )
    entities_text = "\n".join(entity_lines)

    type_groups: dict[str, list[int]] = {}
    for e in entities:
        type_groups.setdefault(e.entity_type, []).append(e.id)
    type_summary_parts = []
    for t, ids in type_groups.items():
        type_summary_parts.append(f"  {t}: {ids}")
    type_summary = "\n".join(type_summary_parts)

    return f"""\
## 实体列表（共 {len(entities)} 个）
{entities_text}

## 各类型 ID 汇总
{type_summary}

## 关系建立任务

为所有实体间的逻辑关系建立有向边：
1. 边方向 = 逻辑依赖方向（前提 → 结论）
2. 每个事实连接到它直接支撑的最近结论
3. 核心结论（is_core=true）应该是叶子节点
4. edge_type 必须匹配 source 和 target 的 entity_type

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "edges": [
    {{
      "source_id": 0,
      "target_id": 1,
      "edge_type": "fact_supports_conclusion"
    }}
  ]
}}
```
"""
