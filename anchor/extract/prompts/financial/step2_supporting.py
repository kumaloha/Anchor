"""
v6 Step 2 — 相关支撑实体扫描
==============================
给定文章和论证锚点（核心结论 + 关键理论），扫描全文提取相关支撑实体。

输出 JSON：
{
  "facts": [...],
  "sub_conclusions": [...],
  "assumptions": [...],
  "predictions": [...],
  "solutions": [...]
}
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import CoreConclusion, KeyTheory

SYSTEM = """\
你是一名专业的论证分析师。给定文章和论证锚点，扫描全文提取所有相关支撑实体。

【语言要求】
所有输出字段必须使用中文，无论原文是何语言。

【相关性测试】
对每个候选实体问：「删掉这个实体，任何核心结论或关键理论的论证会被削弱吗？」
是 → 提取；否 → 跳过。
不限制数量，相关性过滤替代数量约束。

【实体类型定义】

1. 事实（facts）— 按「来源」标准
   有外部来源（数据/事件/现象/报道）→ 事实
   ✓ 市场数据/表现、已发生事件、可观察趋势
   ✗ 作者自己的判断（那是结论）

2. 子结论（sub_conclusions）— 按「归因」标准
   作者的解读/判断/归因推理 → 子结论
   ① 解读型：梳理/归纳现状
   ② 归因型：「A导致B」— 即使 A、B 都是事实，因果判断是结论
   关键词：意味着/说明/因此/体现了/导致/源于

3. 假设（assumptions）
   作者明确标出的条件性前提：「如果X则...」「假设X成立」

4. 预测（predictions）
   明确指向未来：「将/会/预计/到X年」
   仅提取与核心结论/理论直接相关的前瞻性断言

5. 方案（solutions）
   作者给出的具体行动建议或应对策略。
   包括但不限于：
   · 投资/交易建议：买入/卖出/持有/对冲/分散
   · 风险应对策略：撤离/转移资产/离岸配置/资本保护
   · 政策建议：作者建议决策者应采取的措施
   · 个人准备措施：搬迁/规避风险区域/提前行动
   ⚠️ 必须充分提取：如果作者用大量篇幅讨论「面对该局面该怎么办」，
   每条独立的行动建议都应作为单独的 solution 提取

6. 问题（problems）
   作者识别的核心问题、痛点、矛盾或挑战。
   ✓ 明确指出的困境/难题/瓶颈/风险
   ✓ 需要解决的现状问题
   ✓ 行业/市场/社会面临的核心挑战
   问题是「问题→解法→效果→局限」链路的起点

7. 效果（effects）
   解法/方案的预期效果或实际效果。
   ✓ 方案实施后的预期收益/影响
   ✓ 已实施方案的实际效果
   ✓ 政策/措施的正面或负面效果

8. 局限（limitations）
   解法/方案/结论的局限性、风险或约束条件。
   ✓ 方案的适用范围限制
   ✓ 方案可能带来的副作用
   ✓ 结论成立的前提条件/边界

【事实与结论严格分离】
  当作者写「因为X（事实），所以Y（判断）」时，必须拆为：
  ✓ 事实：X（事实描述）
  ✓ 子结论：Y（判断）
  ✗ 合并为一条

  示例：
    原文：「左右翼媒体已分别与同阵营政治力量结盟，复制了1930年代宣传机制」
    ✓ 事实：「左右翼媒体已分别与同阵营政治力量结盟」
    ✓ 子结论：「当前媒体格局复制了1930年代纳粹/苏联的宣传机制」

【粒度规则】
  不同实体、不同逻辑步骤必须分开。
  涉及不同主体的事实，一律拆成独立条目。
  关系型事实（X是Y的资金来源、X依赖Y）必须单独提取。

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(
    content: str,
    core_conclusions: List[CoreConclusion],
    key_theories: List[KeyTheory],
    starting_id: int,
) -> str:
    anchors_parts = []
    for cc in core_conclusions:
        anchors_parts.append(f"  [核心结论 #{cc.id}] {cc.summary}：{cc.claim}")
    for kt in key_theories:
        anchors_parts.append(f"  [关键理论 #{kt.id}] {kt.summary}：{kt.claim}")
    anchors_text = "\n".join(anchors_parts) if anchors_parts else "  （无）"

    return f"""\
## 论证锚点
{anchors_text}

## 文章内容
{content}

## 提取任务

扫描全文，提取与上述锚点相关的支撑实体。
ID 编号从 {starting_id} 开始。

相关性测试：「删掉这个实体，任何核心结论或关键理论的论证会被削弱吗？」
是 → 提取；否 → 跳过。

【关键检查】：
1. 对每个子结论，确认它有独立的事实支撑
2. 不同主体的事实必须分开提取
3. 「A导致B」→ A（事实）+ B（子结论），不合并

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "facts": [
    {{
      "id": {starting_id},
      "claim": "事实陈述（≤120字）",
      "summary": "≤15字摘要",
      "verifiable_statement": "单句可核实陈述",
      "temporal_type": "retrospective",
      "temporal_note": null
    }}
  ],
  "sub_conclusions": [
    {{
      "id": {starting_id + 1},
      "claim": "结论陈述（≤120字）",
      "summary": "≤15字摘要",
      "verifiable_statement": "单句可核实陈述",
      "author_confidence": "certain|likely|uncertain|speculative"
    }}
  ],
  "assumptions": [
    {{
      "id": {starting_id + 2},
      "condition_text": "假设条件（≤120字）",
      "summary": "≤15字摘要",
      "verifiable_statement": null
    }}
  ],
  "predictions": [
    {{
      "id": {starting_id + 3},
      "claim": "预测陈述（≤120字）",
      "summary": "≤15字摘要",
      "temporal_note": "时间范围",
      "author_confidence": "certain|likely|uncertain|speculative"
    }}
  ],
  "solutions": [
    {{
      "id": {starting_id + 4},
      "claim": "行动建议（≤120字）",
      "summary": "≤15字摘要",
      "action_type": "buy|sell|hold|short|diversify|hedge|reduce|advocate",
      "action_target": "行动标的",
      "action_rationale": "推导依据"
    }}
  ],
  "problems": [
    {{
      "id": {starting_id + 5},
      "claim": "问题陈述（≤120字）",
      "summary": "≤15字摘要",
      "problem_domain": "问题领域（可选）"
    }}
  ],
  "effects": [
    {{
      "id": {starting_id + 6},
      "claim": "效果陈述（≤120字）",
      "summary": "≤15字摘要",
      "effect_type": "positive|negative|mixed|uncertain"
    }}
  ],
  "limitations": [
    {{
      "id": {starting_id + 7},
      "claim": "局限陈述（≤120字）",
      "summary": "≤15字摘要"
    }}
  ]
}}
```
"""
