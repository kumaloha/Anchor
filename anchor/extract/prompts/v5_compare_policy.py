"""
v5 政策比对提示词 — 双文档对比标注 change_type
================================================
输入：当年政策列表 + 上年政策列表
输出：PolicyComparisonResult（见 schemas.py）

change_type 枚举：
  新增 — 上年不存在，今年新增
  调整 — 上年存在，今年有实质变化（措辞/数值/力度）
  延续 — 上年存在，今年基本保持不变

deleted_summaries — 上年有、今年完全删除的政策摘要（每条≤30字）
"""

from __future__ import annotations

SYSTEM = """\
你是政策对比分析师。给定同类政策文件的两年版本（当年 + 上年），
对当年每条政策标注其相对于上年的变化类型，并列出上年有而今年删除的政策。

change_type 判断规则：
  新增 — 上年政策列表中没有语义相近的条目
  调整 — 上年有语义相近条目，但今年存在实质变化（数值调整/措辞强化弱化/目标变更等）
  延续 — 上年有语义相近条目，今年基本相同（可有细微文字差异）

deleted_summaries — 上年有、今年完全不再出现的政策，每条≤30字摘要。

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(
    current_year: str,
    current_policies: list[dict],
    prior_year: str,
    prior_policies: list[dict],
) -> str:
    """构建比对提示词的用户消息。

    Args:
        current_year: 当年年份字符串（如 "2026"）
        current_policies: 当年政策列表，每项含 id, theme, summary, policy_text, metric_value
        prior_year: 上年年份字符串（如 "2015"）
        prior_policies: 上年政策列表，每项含 theme, summary, policy_text, metric_value
    """
    def format_policy_list(policies: list[dict]) -> str:
        lines = []
        for p in policies:
            metric = f"（{p['metric_value']}）" if p.get("metric_value") else ""
            id_str = f"[id={p['id']}] " if "id" in p else ""
            lines.append(
                f"  {id_str}[{p.get('theme', '未分类')}] {p.get('summary', '')}{metric}："
                f"{p.get('policy_text', '')}"
            )
        return "\n".join(lines) if lines else "  （无）"

    current_list_str = format_policy_list(current_policies)
    prior_list_str = format_policy_list(prior_policies)

    return f"""\
## 当年（{current_year}）政策条目（共 {len(current_policies)} 条）
{current_list_str}

## 上年（{prior_year}）政策条目（共 {len(prior_policies)} 条，供对比参考）
{prior_list_str}

## 比对任务

对当年每条政策（按 id）标注 change_type（新增/调整/延续）和 change_note（≤30字，仅调整时填写）。
列出上年有而当年完全删除的政策摘要（deleted_summaries，每条≤30字）。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "annotations": [
    {{"policy_id": 1, "change_type": "调整", "change_note": "赤字率由3%升至4%"}},
    {{"policy_id": 2, "change_type": "新增", "change_note": null}},
    {{"policy_id": 3, "change_type": "延续", "change_note": null}}
  ],
  "deleted_summaries": [
    "稳住楼市股市",
    "降低存量房贷利率"
  ]
}}
```
"""
