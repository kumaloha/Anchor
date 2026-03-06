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
你是一名专业的内容摘要分析师。你的任务是将一篇文章的论证结构浓缩为 2–3 句话的叙事摘要。

【摘要要求】
- 长度：2–3 句话，合计不超过 120 字
- 必须体现：核心驱动因素（因）→ 作者的核心判断（果）
- 若文章有多条独立论证主线，每条主线各写一句
- 用陈述句，不加引号、括号、作者称谓
- 具体点出文章实际讨论的资产/行业/机构名称

【资本流向分化规则】（重要）
若关键事实中出现以下两种资产：
  A. 防御型资产（如HALO重资产行业）—— 以「回避AI颠覆」为由吸引资金
  B. AI上游受益资产（如有色金属、电力设备）—— 以「受益于AI需求」而非「回避AI」为由吸引资金

则摘要必须同时体现 A 和 B 两条资金流向，且明确说明两者逻辑不同：
  A 是避险逻辑，B 是低风险AI受益逻辑（而非高风险直接AI敞口）

示例：
  若核心结论 = 「HALO是避险非范式转变」「私募信贷类次贷警号亮」
  且关键事实包含「有色金属/电力设备属AI受益逻辑非HALO防御」
  则正确摘要：
    「AI投资逻辑未根本改变，资本出现三路分化：转向HALO重资产防御AI颠覆风险、
     转向有色金属和电力设备等AI需求上游产业获取低风险AI敞口、
     而深度押注AI基建的私募信贷则已现流动性裂缝，全面危机暂未至。」

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
## 核心结论（★CORE）
{core_text}

## 子结论（中间推断）
{sub_text}

## 关键事实（支撑依据）
{facts_text}

## 摘要任务

基于上述论证结构，用 1–2 句话概括文章的核心论证逻辑（因→果，具体到文章实际讨论的对象）。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "summary": "1–2句叙事摘要（≤80字）"
}}
```
"""
