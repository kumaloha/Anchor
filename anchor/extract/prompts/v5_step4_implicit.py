"""
v5 Step 4 — 隐含条件生成
==========================
目标：对每条推断（前提 → 结论）检查逻辑完备性，
      仅在存在逻辑跳跃时生成隐含条件。

输入：每条有入边的推断（格式：[n] source_text → target_text）
输出 JSON：
{
  "implicit_conditions": [
    {
      "summary": "≤15字",
      "condition_text": "隐含前提（≤120字）",
      "target_claim_id": 2,
      "is_obvious_consensus": false
    }
  ]
}
"""

from __future__ import annotations

from typing import List, Tuple

SYSTEM = """\
你是一名专业的逻辑分析师，擅长识别论证中的隐含前提（未说出但依赖的背景假设）。
所有输出字段（summary、condition_text）必须使用中文。

核心任务有两类：

【A类】逻辑推断（前提 → 结论）：
对每条推断判断是否完备：
- 推断完备（前提直接推出结论，无逻辑跳跃）→ 不生成隐含条件
- 推断存在逻辑跳跃（需要额外的背景假设才能成立）→ 生成一条隐含条件

【B类】独立主张（无前提节点的源结论）：
这些是作者直接提出的核心断言，在文中未由其他观点推导而来。
必须为每条独立主张生成一条"基础前提假设"——即该断言成立所隐含的最关键背景条件或经验前提。
【B类必须生成条目，不可跳过】

【隐含条件的本质】
隐含条件是一个未被作者说出的背景前提，它在逻辑上位于"前提"和"结论"之间，
使得"前提 → 结论"这一跳跃得以成立。

隐含条件的三个必要特征：
  A. 在原文中未被明确陈述
  B. 在逻辑上先于结论（是使结论成立的条件，而非结论本身）
  C. 与结论表达不同的判断（不得与目标结论同义或近义）

【严禁以下情况】
✗ 把结论本身换个说法作为隐含条件
  错误示例：推断"重资产护城河深 → HALO是避险交易"
            错误隐含条件："HALO是避险而非范式转变"（这与结论同义，不是前提）
            正确隐含条件："护城河深度不等于盈利能力，投资者会将其定价为防御资产"

✗ 生成与结论高度重叠的摘要
✗ 为已完备的推断强行生成隐含条件

其他规则：
- condition_text ≤120字，表述具体明确
- is_obvious_consensus=true：对显而易见的共识（如"利率上升增加融资成本"）
- 每条推断最多生成1条隐含条件（取最关键的跳跃点）
- 若推断已完备，不生成任何条目

输出合法 JSON，不加任何其他文字。\
"""


_SOURCE_MARKER = "[独立主张]"


def build_user_message(
    inferences: List[Tuple[str, str, int]]  # (source_text, target_text, target_claim_id)
) -> str:
    regular = [(s, t, tid) for s, t, tid in inferences if s != _SOURCE_MARKER]
    standalone = [(s, t, tid) for s, t, tid in inferences if s == _SOURCE_MARKER]

    parts: list[str] = []
    if regular:
        reg_text = "\n".join(
            f"推断[{i}]（→节点{tgt_id}）：{src_text} → {tgt_text}"
            for i, (src_text, tgt_text, tgt_id) in enumerate(regular)
        )
        parts.append(f"## 【A类】逻辑推断（共 {len(regular)} 条）\n\n{reg_text}")
    if standalone:
        sta_text = "\n".join(
            f"独立主张[{i}]（→节点{tgt_id}）：{tgt_text}"
            for i, (_, tgt_text, tgt_id) in enumerate(standalone)
        )
        parts.append(f"## 【B类】独立主张（共 {len(standalone)} 条，必须逐条生成隐含条件）\n\n{sta_text}")

    body = "\n\n".join(parts)

    return f"""\
{body}

## 隐含条件生成任务

A类推断：完备则跳过，存在跳跃则生成一条隐含条件。
B类独立主张：必须为每条生成一条基础前提假设（不可跳过）。

每条隐含条件须包含：
- summary：≤15字的高度浓缩摘要
- condition_text：隐含前提陈述（≤120字）
- target_claim_id：对应推断中的"→节点X"的 X 值
- is_obvious_consensus：显而易见的共识填 true，非显而易见填 false

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "implicit_conditions": [
    {{
      "summary": "≤15字摘要",
      "condition_text": "隐含前提陈述（≤120字）",
      "target_claim_id": 2,
      "is_obvious_consensus": false
    }}
  ]
}}
```

若所有推断均完备，输出：
```json
{{"implicit_conditions": []}}
```
"""
