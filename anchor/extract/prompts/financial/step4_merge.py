"""
v6 Step 4 — 合并决策
=====================
扫描实体列表，找出语义重复的实体对，输出合并指令。

输入：TypedEntity 列表
输出 JSON：
{
  "merges": [
    {"keep_id": 3, "remove_id": 7, "merged_claim": "...", "merged_summary": "...", "reason": "..."}
  ]
}

不输出实体列表。合并操作由 Python 代码执行。
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import TypedEntity

SYSTEM = """\
你是一名专业的数据清洗师。扫描实体列表，找出语义相同的实体对。

【语言要求】
所有输出字段必须使用中文。

【合并规则】
1. 同类型才能合并（fact+fact，conclusion+conclusion 等）
2. 仅合并描述**同一事件/判断**的实体（"语义重复"），而非"相关主题"
3. 合并后的 claim 必须无损覆盖两条实体的信息

【禁止合并的情况】
⚠️ 不同行动建议不得合并：「撤离高风险区域」和「增持黄金」是两条独立的 solution
⚠️ 不同预测不得合并：「内战风险」和「资本管制」是两条独立的 prediction
⚠️ 不同事实不得合并：「政治暴力上升」和「财政赤字扩大」是两条独立的 fact
合并仅限于：两条实体说的是**完全相同的事情**，只是措辞不同

【归一化要求（对 merged_claim 和 merged_summary）】
- 用标准金融/经济/政治术语（wealth gap→贫富差距，civil war→内战，capital controls→资本管制）
- 优先使用机构全称（"美联储" 而非 "央行"，除非上下文明确）
- 数据引用保留来源（"Pew Research 2025调查"）
- 不造词：不使用"巨富差距"等非标准表达

【输出要求】
只输出 merges 数组。若没有任何可合并的实体对，输出空数组。
不要输出实体列表。

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

## 合并任务

扫描列表，找出语义重复的实体对。
注意：不同主题/不同行动/不同预测不可合并。

若有可合并的实体对，输出合并指令；若没有，输出空数组。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "merges": [
    {{
      "keep_id": 3,
      "remove_id": 7,
      "merged_claim": "合并后的陈述（≤120字）",
      "merged_summary": "合并后的摘要（≤15字）",
      "reason": "两条都描述美联储购债"
    }}
  ]
}}
```
"""
