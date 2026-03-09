"""
v5 Step 5 — 叙事摘要生成
=========================
目标：基于提取出的核心结论和关键事实，用 1–2 句话概括文章的核心论证逻辑。

输入：核心结论列表 + 关键事实列表 + 子结论列表
输出 JSON：
{
  "summary": "..."
}
"""

from __future__ import annotations

from typing import List, Tuple

SYSTEM = """\
用中文写一句话概括这篇文章的核心观点，20-40字。
输出必须是中文，无论输入内容是何语言。

要求：
- 点出最重要的实体（人/市场/资产/机构）和它们之间的关系或变化
- 若文章包含"当前处于周期哪个阶段"的判断 + 具体投资建议，两者都要体现
- 不写细节、数字、例子
- 如果有多条独立逻辑，选最重要的一条，或用「；」连接两条

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(
    core_conclusions: List[str],
    sub_conclusions: List[str],
    key_facts: List[str],
) -> str:
    core_text = "\n".join(f"  - {c}" for c in core_conclusions) or "  （无）"
    sub_text = "\n".join(f"  - {c}" for c in sub_conclusions) or "  （无）"
    facts_text = "\n".join(f"  - {f}" for f in key_facts) or "  （无）"

    return f"""\
核心结论：{core_text}
推导结论：{sub_text}
关键事实：{facts_text}

```json
{{
  "summary": "≤25字"
}}
```
"""
