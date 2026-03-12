"""
通用判断 — Step 2b：政策纵向对比分析
=====================================
仅对 content_type in (政策宣布, 政策解读) 触发。

逻辑：
  - 通过 Serper 搜索上一年同类政策的原文摘要
  - LLM 对比今年 vs 去年的措辞变化、新增/删减、侧重点转移
  - 输出 policy_delta（变化摘要）和基于对比的 intent_note

输出 JSON：
  {
    "policy_delta": "与去年相比的核心变化（≤150字）",
    "intent_note":  "基于政策对比分析的作者意图说明（≤100字）"
  }

若未找到上一年政策，policy_delta 填 null，intent_note 维持原值。
"""

from __future__ import annotations

SYSTEM = """\
你是一名专业的中国政策分析师，擅长通过纵向对比识别政策意图的转变。

【核心方法】
政策文件的真实意图往往不在其字面内容，而在与上一年同类政策的"变化"上：
  - 新增的表述 → 今年重点关注的方向
  - 删除的表述 → 去年强调而今年刻意回避的问题
  - 量化目标的上调/下调 → 预期的收紧/放松
  - 措辞从"积极"改为"稳健" → 政策节奏的转变
  - 新概念（如"新质生产力"）首次出现 → 战略重心迁移

【输出规范】
policy_delta: 今年政策与去年相比的核心变化（新增/删减/调整）≤150字
intent_note:  基于以上对比，推断发布此内容的政策意图，重点说明"变化背后的目的"≤100字

若上一年政策信息不充分，policy_delta 写 null，intent_note 基于现有内容给出最佳判断。
输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(
    current_content: str,
    content_topic: str,
    current_year: int,
    prior_year_content: str | None,
) -> str:
    prior_section = (
        f"## 上一年（{current_year - 1}年）同类政策内容（搜索获取）\n{prior_year_content}"
        if prior_year_content
        else f"## 上一年（{current_year - 1}年）同类政策\n（未能获取，请基于训练知识进行对比）"
    )

    return f"""\
## 分析主题
{content_topic}

## 今年（{current_year}年）政策内容（摘要）
{current_content[:2000]}{"..." if len(current_content) > 2000 else ""}

{prior_section}

## 分析任务
1. 找出今年与去年在措辞、目标数值、概念使用上的核心变化
2. 基于变化判断政策意图的转变方向

严格输出 JSON：

```json
{{
  "policy_delta": "与去年相比的核心变化，≤150字，如无信息则填null",
  "intent_note": "基于政策对比的意图说明，≤100字"
}}
```\
"""
