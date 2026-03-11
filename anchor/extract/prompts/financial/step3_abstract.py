"""
v6 Step 3 — 抽象简化
======================
对每个实体的 claim 和 summary 进行抽象简化，确保主体/对象明确。

输入：TypedEntity 列表
输出 JSON：
{
  "entities": [...]
}
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import TypedEntity

SYSTEM = """\
你是一名专业的内容编辑。对每个实体的 claim 和 summary 进行抽象简化。

【语言要求】
所有输出字段必须使用中文。

【核心规则】
每个表达必须包含明确的主体或对象：
  ✗ "经济不好" → ✓ "美国经济处于衰退期"
  ✗ "利率上升" → ✓ "美联储将联邦基金利率上调25基点"
  ✗ "政治暴力加剧" → ✓ "美国国内政治暴力事件十年增长十倍"
  ✗ "需求增加" → ✓ "中国有色金属进口需求同比增长15%"

【简化规则】
1. 简化但不丢失关键信息
2. claim ≤120字，summary ≤15字
3. 使用标准术语（金融/经济/政治领域通用表达）
4. 同步更新 verifiable_statement（如有），确保可核实
5. 保持所有其他字段不变（id、entity_type、is_core、author_confidence、temporal_type、temporal_note、action_type、action_target、action_rationale、condition_text）——原值是什么就输出什么，null 则输出 null

【术语归一化（必须遵守）】
  翻译英文概念时，必须使用中文学术/媒体标准术语：
  · wealth gap / wealth inequality → 贫富差距（不是"巨富差距"）
  · income inequality → 收入不平等
  · fiscal deficit → 财政赤字
  · sovereign debt → 主权债务
  · capital controls → 资本管制
  · civil war → 内战
  · revolution → 革命
  · populism → 民粹主义
  如果不确定标准译法，使用中文经济学教科书中最常见的表达。

【特别注意】
- 对于 assumption 类型，精炼 condition_text 字段而非 claim 字段
- summary 必须高度抽象，能让读者一眼抓住要点
- 不要改变实体的语义，只改善表达精度

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(entities: List[TypedEntity]) -> str:
    entity_lines = []
    for e in entities:
        text = e.condition_text if e.entity_type == "assumption" else e.claim
        entity_lines.append(
            f"  [{e.id}] ({e.entity_type}{', core' if e.is_core else ''}) "
            f"{e.summary}：{text}"
        )
    entities_text = "\n".join(entity_lines)

    return f"""\
## 实体列表（共 {len(entities)} 个）
{entities_text}

## 精炼任务

对每个实体的 claim/condition_text 和 summary 进行抽象简化：
1. 确保每个表达包含明确的主体或对象
2. 使用标准术语
3. 同步更新 verifiable_statement
4. 保持 id、entity_type、is_core 和其他字段不变

请严格按以下 JSON 格式输出全部实体，不要输出任何其他内容：

```json
{{
  "entities": [
    {{
      "id": 0,
      "entity_type": "fact|conclusion|theory|prediction|solution|assumption",
      "claim": "精炼后的陈述（≤120字）",
      "summary": "精炼后的摘要（≤15字）",
      "is_core": false,
      "verifiable_statement": "精炼后的可核实陈述 或 null",
      "author_confidence": "（保留原值）",
      "temporal_type": "（保留原值）",
      "temporal_note": "（保留原值）",
      "action_type": "（保留原值）",
      "action_target": "（保留原值）",
      "action_rationale": "（保留原值）",
      "condition_text": "（保留原值）"
    }}
  ]
}}
```
"""
