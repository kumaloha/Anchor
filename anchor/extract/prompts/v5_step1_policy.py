"""
v5_step1_policy.py — 政策文档提取（v3 六维属性 + 手段子条目）
================================================================
两步提取：
  Step A: 轻量主旨扫描（单 LLM call，~300 tokens 输出）
          输出 theme 名称列表，用于并行联网搜索

  Step B: 完整提取（输入：当年全文 + 上年全文 + 各主旨联网搜索摘要）
          输出 PolicyExtractionResult（见 schemas.py）

六维属性：
  theme / target / intensity / intensity_note / background / organization

手段子条目（PolicyMeasure）：
  summary / measure_text / trend（升级|降级|延续|新增|删除）/ trend_note

兼容旧接口：
  SYSTEM + build_user_message() — 保留供 _step1_claims_policy 调用（旧流程不删除）
"""

from __future__ import annotations

# ===========================================================================
# 旧接口（保留不删，供 _step1_claims_policy 调用）
# ===========================================================================

SYSTEM = """\
你是一名专业的政策分析师，擅长从政策文件中提取结构化信息。
你的任务是按4维提取框架（背景目的、定调术语、关键指标、组织保障）解析政策文本，输出结构化JSON。
注意：发文机关由其他步骤识别，本步骤不提取。

【政策主旨（themes）】
  每个 theme 对应文章中的一个大类政策方向。
  theme 字段：
    theme_name         — 简短名称（≤6字）
    background         — 背景与目的（≤200字）
    enforcement_note   — 组织保障（≤80字）
    has_enforcement_teeth — 是否纳入考核（true/false）
    policies           — 该主旨下的具体政策条目列表

【政策条目（policies）】
  policy 字段：
    summary / policy_text / urgency（mandatory|encouraged|pilot|gradual）
    metric_value / target_year / is_hard_target

【变化标注事实（facts）】格式：{id, text, summary}
【总体政策方向结论（conclusions）】格式：{id, text, summary}，id 从 100 开始

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(
    content: str,
    platform: str,
    author: str,
    today: str,
    author_intent: str | None = None,
) -> str:
    intent_line = f"\n作者意图（预判）：{author_intent}" if author_intent else ""

    return f"""\
## 分析上下文
当前日期：{today}
平台：{platform}
来源：{author}{intent_line}

## 政策文本
{content}

## 提取任务

Step A — 相关性判断
  此内容是否为可分析的政策文件（政府工作报告/政策公告/会议决议等）？
  若否，设 is_relevant_content=false。

Step B — 提取政策主旨（themes，3-6个）
Step C — 提取变化标注事实（facts，3条以内）
Step D — 提取总体政策方向结论（conclusions，2-3条，id 从 100 开始）

输出格式：
```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "themes": [
    {{
      "theme_name": "财政政策",
      "background": "...",
      "enforcement_note": "...",
      "has_enforcement_teeth": true,
      "policies": [
        {{
          "summary": "赤字率升至4%",
          "policy_text": "拟按4%左右安排赤字率",
          "urgency": "mandatory",
          "metric_value": "4%",
          "target_year": "2026",
          "is_hard_target": true
        }}
      ]
    }}
  ],
  "facts": [{{"id": 0, "text": "...", "summary": "..."}}],
  "conclusions": [{{"id": 100, "text": "...", "summary": "..."}}]
}}
```
"""


# ===========================================================================
# Step A: 主旨扫描（新流程）
# ===========================================================================

SYSTEM_THEME_SCAN = """\
你是一名政策分析师。请快速扫描政策文本，识别其中涉及的主要政策方向（主旨名称列表）。
只输出主旨名称，不做任何深度分析。

输出格式（合法 JSON，不加任何其他内容）：
{"themes": ["财政政策", "货币政策", "绿色低碳", "科技创新"]}

要求：
- 主旨名称 ≤8字，简洁准确
- 数量：3-20个，覆盖文件所有主要政策方向，不得遗漏
- 经济类：财政、货币、产业、科技、民生、外贸、绿色低碳、房地产等（如有）
- 政治/安全类：国防建设、两岸关系、对台政策、外交、国家安全等（只要文中出现必须列入）
- 文末的政治/外交/安全章节与经济章节同等重要，不得因篇幅在后而省略
- 篇幅短但语气强硬（如"坚决反对""绝不允许""严厉打击"）或主题敏感（两岸、国家安全、领土主权等）的方向，必须保留，不因字数少而忽略
- 不做内容提取，不输出任何解释\
"""


def build_theme_scan_message(content: str) -> str:
    # 覆盖全文：取首段 + 尾段，确保文末政治/外交/安全议题不遗漏
    if len(content) <= 8000:
        text_block = content
    else:
        text_block = content[:5000] + "\n\n[...中间部分省略...]\n\n" + content[-3000:]
    return f"""\
## 政策文本（首尾覆盖，约8000字）
{text_block}

请快速识别上述文本涉及的主要政策方向（主旨名称列表），输出 JSON，不加任何其他内容。\
"""


# ===========================================================================
# Step B: 完整提取（新流程）
# ===========================================================================

SYSTEM_FULL_EXTRACT = """\
你是一名顶级政策研究员，擅长解读政府工作报告、政策公告等文件，并通过历年纵向对比揭示政策演变。
你的任务是对当年政策文本做全面六维提取，并与上年文本逐项对比，将"同比变化"内嵌到每个字段的描述中。

═══════════════════════════════════════════════════════════
一、Policy（政策主旨）— 每个大类方向一条
═══════════════════════════════════════════════════════════
字段说明：

  theme          — 主旨名称，≤8字（如"绿色低碳""财政政策"）

  change_summary — 一句话变化总结，≤50字，概括该主旨下所有核心变化
                   例："力度由强转中，双控目标收紧，组织保障新增发改委"
                   若无上年则描述当年定调，不加"相比"字样

  target       — 目标描述（量化/定性），≤100字
                 如有上年对比，在括号中注明变化：
                 例："单位GDP能耗下降3%（相比去年1%要求明显提高）"
                 若无上年对应内容，直接描述目标，不加对比括号

  intensity    — 力度枚举（三选一），核心判断依据是政策紧迫程度而非措施条数：
                 strong   — 文本中存在危机信号：承认问题仍在恶化（"仍在调整""止跌""尚未企稳"）、
                            使用救市/托底语言（"止跌回稳""坚决遏制""托住底线"）、首次强力介入。
                            ⚠️ 即使措施较少，有危机语言即判 strong
                 moderate — 问题已趋稳，政策进入常态化管理/优化阶段（"稳定""巩固""进一步完善"）
                 weak     — 探索/试点/鼓励性表述，无明确压力
                 跨年规则：上年危机语言→当年常态化 = 上年 strong / 当年 moderate（即使当年措施更多）

  intensity_note — 力度说明，≤80字，必须包含定调术语依据
                   如有上年对比，说明升降变化：
                   例："'强化管控'替代去年'稳步推进'，要求更为明确"

  background   — 背景分析，≤200字
                 综合：① 文件内容推断的政策动因；② 联网搜索摘要（若提供）；③ 与上年的背景变化
                 例："AI数据中心激增推高能耗（相比去年高耗能行业扩展至AI基建）"

  organization — 组织保障，≤100字
                 谁牵头、是否纳入考核/问责
                 如有上年对比，说明变化：
                 例："生态环境部、发改委联合督导（相比去年新增发改委参与）"

═══════════════════════════════════════════════════════════
二、PolicyMeasure（手段子条目）— 每条具体措施一条
═══════════════════════════════════════════════════════════
字段说明：

  summary      — ≤15字措施摘要（如"碳排放双控""超常规逆周期调节"）
  measure_text — 具体措施内容，≤150字，忠实原文，可适当浓缩
  trend        — 与上年对比趋势（五选一）：
                 升级 — 要求更严/范围更广/力度更强
                 降级 — 要求更松/范围收窄/力度减弱
                 延续 — 内容基本相同，无实质变化
                 新增 — 当年新出现，上年无对应
                 删除 — （不在此处使用，删除项在 facts 中记录）
                 若无上年文本，统一标"新增"
  trend_note   — 变化说明，≤30字（可选），延续/无上年可填 null
                 例："由强度控制升级为双控（量+强度）"

每个 policy 下的 measures 数量：2-6条，覆盖该主旨的主要具体措施

═══════════════════════════════════════════════════════════
三、facts（变化标注事实，0-4条）
═══════════════════════════════════════════════════════════
收录：① 上年有、当年删除的重要政策表述（用[删除]前缀）
      ② 文件中明确点名的重大政策变化
格式：{"id": 0, "text": "≤120字", "summary": "≤15字，可加[删除][新增]前缀"}

═══════════════════════════════════════════════════════════
四、conclusions（总体政策方向结论，2-3条）
═══════════════════════════════════════════════════════════
对当年整体政策取向的高层次判断。
格式：{"id": 100, "text": "≤120字", "summary": "≤15字"}
id 从 100 开始，避免与 facts 冲突。

═══════════════════════════════════════════════════════════
重要规则：
- 若无上年文本：trend 一律标"新增"，括号对比说明一律省略
- 各字段不得超过字数限制
- 只输出合法 JSON，不加任何解释或 markdown 代码块
═══════════════════════════════════════════════════════════\
"""


def build_full_extract_message(
    current_content: str,
    prior_content: str | None,
    web_ctx: dict[str, str],
    themes: list[str] | None = None,
) -> str:
    """构建完整提取的用户消息。

    Args:
        current_content: 当年政策文件全文
        prior_content:   上年政策文件全文（None 则省略）
        web_ctx:         联网搜索结果 {"主旨名称": "搜索摘要..."}
        themes:          Step A 扫描出的主旨列表，传入后强制覆盖
    """
    section_current = f"## 当年政策文本\n{current_content[:30000]}"

    if prior_content:
        section_prior = f"\n\n## 上年政策文本（用于纵向对比）\n{prior_content[:20000]}"
    else:
        section_prior = '\n\n## 上年政策文本\n（未提供，trend 一律标"新增"，不写对比括号）'

    if web_ctx:
        ctx_lines = "\n".join(
            f"  【{theme}】{snippet}"
            for theme, snippet in web_ctx.items()
            if snippet
        )
        section_web = f"\n\n## 各主旨联网搜索背景摘要\n{ctx_lines}"
    else:
        section_web = ""

    if themes:
        theme_list = "、".join(themes)
        section_themes = f"\n\n## Step A 已识别的主旨列表（必须全部提取，不得遗漏）\n{theme_list}"
    else:
        section_themes = ""

    return f"""\
{section_current}{section_prior}{section_web}{section_themes}

## 提取任务

Step 1 — 相关性判断
  此内容是否为可分析的政策文件（政府工作报告/政策公告/会议决议等）？
  若否，设 is_relevant_content=false，其余字段留空。

Step 2 — 逐一提取上方"Step A 已识别的主旨列表"中的每一个主旨，一个都不能少
  对每个主旨提取六维属性：theme / target / intensity / intensity_note / background / organization
  以及手段子条目列表 measures[]
  所有含同比对比的字段，直接在括号内嵌入变化说明
  ⚠️ 政治/外交/安全类主旨（两岸关系、国防、外交、国家安全等）与经济类同等对待，必须提取

Step 3 — 提取变化标注事实（facts，0-4条）

Step 4 — 提取总体政策方向结论（conclusions，2-3条，id 从 100 开始）

输出格式（严格遵守，只输出 JSON，不加 markdown 代码块）：
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "policies": [
    {{
      "theme": "绿色低碳",
      "target": "单位GDP能耗下降3%（相比去年1%明显提高）",
      "intensity": "strong",
      "intensity_note": "从'稳步推进'升级为'强化管控'，措辞更为强硬",
      "background": "AI数据中心激增推高能耗（相比去年高耗能行业扩展至AI基建）；碳中和2060节点压力增加",
      "organization": "生态环境部、发改委联合督导，纳入省级生态文明考核（相比去年新增发改委参与）",
      "measures": [
        {{
          "summary": "碳排放双控",
          "measure_text": "加快构建碳排放双控制度体系，扩大全国碳排放权交易市场行业覆盖范围",
          "trend": "升级",
          "trend_note": "由强度单控升级为双控（量+强度）"
        }},
        {{
          "summary": "新能源基地建设",
          "measure_text": "加快建设'沙戈荒'新能源基地，发展海上风电，统筹就地消纳和外送通道建设",
          "trend": "延续",
          "trend_note": null
        }}
      ]
    }}
  ],
  "facts": [
    {{"id": 0, "text": "删除'稳住楼市股市'表述，改为'防范化解风险'", "summary": "[删除] 稳住楼市股市"}}
  ],
  "conclusions": [
    {{"id": 100, "text": "财政政策由稳健转向积极扩张，赤字率升至历史高位", "summary": "财政大幅扩张"}}
  ]
}}\
"""


# ===========================================================================
# 长文档专用（长度 > LONG_DOC_THRESHOLD）
# ===========================================================================

# ---------------------------------------------------------------------------
# Step A2: 每主旨段落提取
# ---------------------------------------------------------------------------

SYSTEM_PARA_EXTRACT = """\
你是一名政策文本提取助手。
请从政策文件全文中，找出所有与指定政策主旨直接相关的段落和句子，原文照录，不做修改或总结。
若某句话同时涉及多个主旨，也应包含。
如无相关内容，输出"（无相关内容）"。直接输出原文内容，不加任何说明或标注。\
"""


def build_para_extract_message(content: str, theme: str) -> str:
    return f"""\
## 政策文本全文
{content}

## 提取任务
请从上述文本中，找出所有与「{theme}」直接相关的段落和句子，原文照录，段落间用空行分隔。\
"""


# ---------------------------------------------------------------------------
# Step B（单主旨）: 六维提取
# ---------------------------------------------------------------------------

SYSTEM_SINGLE_THEME = """\
你是一名顶级政策研究员。你将获得某一政策主旨的当年原文段落和上年原文段落（可选），\
请分别提取当年和上年的各维度值，输出结构化 JSON。

输出字段说明：
  theme            — 主旨名称（沿用给定名称，不得改动）
  change_summary   — 一句话变化总结 ≤50字，概括该主旨下所有核心变化
                     例："由危机式托底转向常态化管理，双控目标收紧，组织保障新增发改委"
                     若无上年则描述当年定调，不加"相比"字样
  target           — 当年目标（量化/定性，≤100字，不含括号对比）
  target_prev      — 上年目标（≤100字，无上年则 null）
  intensity        — 当年力度：strong | moderate | weak
  intensity_prev   — 上年力度：strong | moderate | weak（无上年则 null）
  intensity_note      — 当年力度说明 ≤60字（定调依据：关键词/紧迫程度）
  intensity_note_prev — 上年力度说明 ≤60字（无上年则 null）
  background       — 当年背景 ≤150字（不含括号对比）
  background_prev  — 上年背景 ≤150字（无上年则 null）
  organization     — 当年组织保障 ≤80字（不含括号对比）
  organization_prev— 上年组织保障 ≤80字（无上年则 null）
  measures         — 手段子条目（2-6条）：summary / measure_text / trend / trend_note

【intensity 判断规则】
intensity 反映的是政策紧迫程度，而非措施条数多少：

  strong   — 文本中隐含或明确存在危机信号：
             • 承认问题仍在恶化（"仍在调整""止跌""尚未企稳""困难较多"）
             • 使用救市/托底语言（"止跌回稳""托住底线""坚决遏制"）
             • 政策从无到有、首次出现此领域的强力介入
             ⚠️ 即使措施描述较少，只要有危机语言，就判 strong

  moderate — 问题已趋于稳定，政策进入管理/优化阶段：
             • 常态化措辞（"稳定""巩固""持续推进""进一步完善"）
             • 危机期已过，转向结构优化（如"去库存""好房子工程"）
             • 措施详细但无紧迫感

  weak     — 鼓励探索性、试点性表述，无明确压力

  【跨年对比规则】
  若上年文本含危机语言、当年转为常态化管理 → 上年 strong，当年 moderate（即使当年措施更多）
  若上年常态、当年出现新危机表述 → 当年 strong，上年 moderate

其他规则：
- 所有 _prev 字段：无上年内容时填 null，trend 统一标"新增"
- 只输出合法 JSON，不加 markdown 代码块\
"""


def build_single_theme_message(
    theme: str,
    curr_paragraphs: str,
    prior_paragraphs: str | None,
    web_snippet: str | None,
) -> str:
    has_prior = prior_paragraphs and prior_paragraphs.strip() and prior_paragraphs.strip() != "（无相关内容）"
    prior_sec = (
        f"\n\n## 上年相关段落\n{prior_paragraphs}"
        if has_prior
        else '\n\n## 上年相关段落\n（未提供，所有 _prev 字段填 null）'
    )
    web_sec = f"\n\n## 联网搜索背景摘要\n{web_snippet}" if web_snippet else ""
    return f"""\
## 主旨：{theme}

## 当年相关段落
{curr_paragraphs}{prior_sec}{web_sec}

请输出 JSON（单个对象，不是数组）：
{{
  "theme": "{theme}",
  "change_summary": "一句话总结该主旨所有核心变化...",
  "target": "当年目标...",
  "target_prev": "上年目标或null",
  "intensity": "strong|moderate|weak",
  "intensity_prev": "strong|moderate|weak或null",
  "intensity_note": "当年力度说明...",
  "intensity_note_prev": "上年力度说明或null",
  "background": "当年背景...",
  "background_prev": "上年背景或null",
  "organization": "当年组织保障...",
  "organization_prev": "上年组织保障或null",
  "measures": [
    {{"summary": "...", "measure_text": "...", "trend": "新增|升级|降级|延续", "trend_note": null}}
  ]
}}\
"""


# ---------------------------------------------------------------------------
# 长文档 facts / conclusions 专项提取
# ---------------------------------------------------------------------------

SYSTEM_FACTS_CONCLUSIONS = """\
你是一名政策分析师。请从政策文件全文中提取：
1. 变化标注事实（facts，0-4条）：上年有当年删除的重要表述（加[删除]前缀），或文件中明确点名的重大变化
2. 总体政策方向结论（conclusions，2-3条）：对当年整体政策取向的高层次判断，id 从 100 开始

只输出合法 JSON，不加任何解释：
{"facts": [{"id": 0, "text": "≤120字", "summary": "≤15字"}], "conclusions": [{"id": 100, "text": "≤120字", "summary": "≤15字"}]}\
"""


def build_facts_conclusions_message(
    current_content: str,
    prior_content: str | None,
) -> str:
    prior_sec = f"\n\n## 上年政策文本（用于对比删除项）\n{prior_content[:8000]}" if prior_content else ""
    return f"""\
## 当年政策文本
{current_content[:15000]}{prior_sec}

请提取变化标注事实和总体政策方向结论，输出 JSON。\
"""
