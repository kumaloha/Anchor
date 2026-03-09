"""
v5 Step 2 — 相似声明合并
===========================
目标：识别语义相似或高度重叠的声明，合并为一条新的综合陈述，消除冗余。
警告：只合并真正相似的声明，不要将描述不同主体/现象的声明合并。

输入：声明列表（来自 Step 1）
输出 JSON：
{
  "merges": [
    {
      "keep": 0,
      "discard": [3],
      "merged_text": "综合两条声明的新陈述（≤120字）",
      "merged_summary": "≤15字摘要"
    }
  ]
}

若无需合并，merges 为空数组。
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas import RawClaim

SYSTEM = """\
你是一名专业的语义分析师。你的任务是识别一组声明中语义高度重叠的声明，
将它们合并为一条新的综合陈述，消除冗余。

【应该合并的情况】
✓ 两条声明描述完全相同的核心主张，只是措辞不同
✓ 两条声明关于同一实体的同一现象，一条是另一条的细节补充（合并后信息无损失）

【不应合并的情况】
✗ 两条声明描述不同的主体（A的风险 vs B的资金来源 → 即使相关也不合并）
✗ 两条声明描述不同的因果关系或逻辑步骤
✗ 合并后 merged_text 超过 100 字仍无法完整表达两条声明的信息（说明它们包含不同信息）
✗ 两条声明互为依据/结论关系（应保留边，而非合并）

【判断方法】
问：「合并后，读者还能完整了解合并前两条声明各自表达的独立信息吗？」
能 → 可以合并；不能 → 不合并。

合并输出：
- merged_text：新的综合陈述，≤120字，无损覆盖全部信息
- merged_summary：≤15字高度概括
- keep：被保留节点的 id
- discard：被废弃节点的 id（一般只废弃1条）

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(claims: List[RawClaim]) -> str:
    claims_text = "\n".join(
        f"  [{c.id}] {c.summary}：{c.text}" for c in claims
    )
    return f"""\
## 声明列表（共 {len(claims)} 条）

{claims_text}

## 合并任务

检查上述声明，只合并语义高度重叠（几乎表达相同内容）的声明对。
宁可少合并，也不要把描述不同事实的声明强行合并。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "merges": [
    {{
      "keep": 0,
      "discard": [3],
      "merged_text": "综合两条声明的新陈述（≤120字）",
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
