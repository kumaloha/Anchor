"""
v5 Step 2 — 同义声明合并
===========================
目标：识别语义完全等价的声明对并合并，消除重复表述。

输入：声明列表（来自 Step 1）
输出 JSON：
{
  "merges": [
    {"keep": 0, "discard": [3], "merged_summary": "合并后摘要（≤15字）"}
  ]
}

若无需合并，merges 为空数组。
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import RawClaim

SYSTEM = """\
你是一名专业的语义分析师。你的任务是识别一组声明中语义完全等价的声明对，
将它们合并为单一节点以消除冗余。

合并规则：
1. 仅当两条声明表达完全相同的判断时才合并（核心主张一致）
2. 措辞不同但意义不同 → 不合并
3. 互补但不完全等价的声明 → 不合并（保留两者）
4. 合并后保留表述更完整/准确的那一条（keep），废弃另一条（discard）
5. 若无任何声明对是语义等价的，返回空 merges 数组
6. 输出合法 JSON，不加任何其他文字\
"""


def build_user_message(claims: List[RawClaim]) -> str:
    claims_text = "\n".join(
        f"  [{c.id}] {c.summary}：{c.text}" for c in claims
    )
    return f"""\
## 声明列表（共 {len(claims)} 条）

{claims_text}

## 合并任务

检查上述声明，识别语义完全等价的声明对。
- 同一个判断用不同措辞表述 → 合并
- 两个独立的、互补的观点 → 不合并

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "merges": [
    {{
      "keep": 0,
      "discard": [3],
      "merged_summary": "合并后摘要（≤15字）"
    }}
  ]
}}
```

若无需合并，输出：
```json
{{"merges": []}}
```
"""
