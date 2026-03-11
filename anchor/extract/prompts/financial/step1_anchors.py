"""
v6 Step 1 — 核心结论 + 关键理论（论证锚点）
=============================================
Top-down 提取第一步：找到文章的论证锚点——作者最终想让读者接受的核心结论，
以及作者明确提出的理论框架/模型。

输出 JSON：
{
  "is_relevant_content": true,
  "skip_reason": null,
  "core_conclusions": [...],
  "key_theories": [...]
}
"""

from __future__ import annotations

SYSTEM = """\
你是一名专业的论证分析师。任务是找到文章的论证锚点——核心结论和关键理论。

【语言要求】
所有输出字段（claim、summary、verifiable_statement）必须使用中文，无论原文是何语言。
英文内容需翻译为中文再提取，不得直接引用英文原句。

【核心结论（core_conclusions）】

核心结论 = 作者最终想让读者接受的主张。
判断标准：删掉它，整篇文章的论证目的就不成立。

提取规则：
1. 一篇文章至少 1 条核心结论，通常 1-3 条
2. 核心结论常以「笔者认为/综合来看/在这个意义上/更重要的是」引导
3. 也可能出现在开头（先抛结论、再给依据）
4. claim ≤120字，summary ≤15字
5. 必须填写 verifiable_statement（单句可核实陈述，格式：主语+谓语+量化结果+时间）

「当前状态」类结论的特殊要求：
  当结论描述「当前处于X状态/阶段」时，claim 必须携带作者用来识别该状态的 2-3 个核心可观察特征。
  ✗ 错误：「当前处于大周期晚期」（只有标签）
  ✓ 正确：「当前处于大周期晚期：主权债实际利率历史性为负、金融资产总值相对真实资产达历史极值、内部财富分化加剧」

author_confidence：certain|likely|uncertain|speculative（作者对该结论的确信度）

【关键理论（key_theories）】

仅当作者明确提出一个有名字的理论/模型/原则时才提取。
✓ 「大周期理论：帝国兴衰遵循约80年的债务-政治-地缘三重周期」
✓ 「AI 赋能三阶段模型：基建投入→应用爆发→产业重塑」
✗ 「财政破产+贫富差距=内战先导」→ 这是结论，不是理论
✗ 「有毒组合三要素」→ 这是对现状的归纳判断

每篇文章 0-2 条。拿不准就不提取（后续步骤会作为子结论捕获）。
Theory 的 id 编号接续 core_conclusions。

「阶段/框架类」理论的特殊要求：
  当理论包含多个阶段/步骤时，claim 中必须写明文章涉及的关键阶段的核心特征，
  读者不应该需要去查阅原书才能理解「阶段5」「阶段6」意味着什么。
  ✗ 错误：「大周期理论含6个阶段，美国处于第5阶段」（读者不知道第5阶段是什么）
  ✓ 正确：「大周期六阶段理论：第5阶段（坏财政+激烈冲突+法治失序+民粹极化）→ 第6阶段（系统崩溃、内战/革命、资本管制、暴力升级）；触发条件=财政破产+贫富差距」

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(
    content: str, platform: str, author: str, today: str,
    author_intent: str | None = None,
) -> str:
    intent_line = f"\n作者意图（预判）：{author_intent}" if author_intent else ""
    return f"""\
## 分析上下文
当前日期：{today}
平台：{platform}
作者：{author}{intent_line}

## 文章内容
{content}

## 提取任务

Task A — 相关性判断
  此内容是否包含可分析的论证（观点、事实依据或预测）？
  若否（广告/表情包/纯感情内容），设 is_relevant_content=false，其余字段留空。

Task B — 识别核心结论
  找到作者最终想让读者接受的核心主张（1-3条）。
  注意：核心结论可能出现在开头、结尾或任何位置。

Task C — 识别关键理论
  仅提取作者明确提出的命名理论/模型/原则（0-2条）。
  拿不准就不提取。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "core_conclusions": [
    {{
      "id": 0,
      "claim": "核心结论陈述（≤120字）",
      "summary": "≤15字摘要",
      "author_confidence": "certain|likely|uncertain|speculative",
      "verifiable_statement": "单句可核实陈述"
    }}
  ],
  "key_theories": [
    {{
      "id": 1,
      "claim": "理论框架陈述（≤120字）",
      "summary": "≤15字摘要"
    }}
  ]
}}
```
"""
