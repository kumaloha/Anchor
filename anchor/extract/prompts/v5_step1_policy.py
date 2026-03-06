"""
v5 Step 1（政策模式）— PolicyTheme + PolicyItem 提取
======================================================
专用于政府工作报告、两会决议、政策公告等政策类文本。

核心任务（4维提取）：
  Chain 1（本步骤）：
    - 背景与目的（为什么是现在）→ PolicyTheme.background（从文件内容推断）
    - 定调术语（紧迫度）→ PolicyItem.urgency
    - 关键指标（硬约束）→ PolicyItem.metric_value + is_hard_target
    - 组织保障 → PolicyTheme.enforcement_note + has_enforcement_teeth
  Chain 2（不在此处）：
    - 发文机关（级别）→ issuing_authority + authority_level
  Chain 3（不在此处）：
    - 政策执行情况追踪 → PolicyItem.execution_status + execution_note

urgency 映射：
  mandatory  — 严禁/必须/不得/强制
  encouraged — 鼓励/支持/推动/引导
  pilot      — 探索/试点/研究
  gradual    — 循序渐进/稳步/有序

change_type 不在此步骤产出，由单独比对 LLM 填写。

输出格式：Step1PolicyResult（见 schemas.py）
"""

from __future__ import annotations

SYSTEM = """\
你是一名专业的政策分析师，擅长从政策文件中提取结构化信息。
你的任务是按4维提取框架（背景目的、定调术语、关键指标、组织保障）解析政策文本，输出结构化JSON。
注意：发文机关由其他步骤识别，本步骤不提取。

【政策主旨（themes）】
  每个 theme 对应文章中的一个大类政策方向。
  政府工作报告通常涵盖以下领域，需全部扫描，不可遗漏：
    经济类：财政、货币、产业、科技、民生、外贸、改革开放、绿色低碳、房地产
    主权与安全类：对台政策（"一个中国""九二共识""反台独""两岸统一"等）、国防军事、外交
  theme 字段：
    theme_name         — 简短名称（≤6字，如"财政政策""对台政策""国防建设"）
    background         — 背景与目的（≤200字，结合文件内容说明为何出台此方向政策、当前面临什么压力）
    enforcement_note   — 组织保障（≤80字，谁牵头、是否纳入考核/问责）
    has_enforcement_teeth — 是否有执行主体且纳入考核（true/false）
    policies           — 该主旨下的具体政策条目列表

【政策条目（policies，嵌套在 theme 内）】
  每条 policy 对应一条具体的政策承诺/计划。
  policy 字段：
    summary      — ≤15字摘要（如"赤字率升至4%"）
    policy_text  — 具体内容（≤120字，忠实原文）
    urgency      — mandatory|encouraged|pilot|gradual
      mandatory  → 严禁/必须/不得/强制/明确要求
      encouraged → 鼓励/支持/推动/引导/积极
      pilot      → 探索/试点/研究/开展试验
      gradual    → 循序渐进/稳步/有序/逐步
    metric_value — 量化值（如"4%""1.3万亿""5%左右"，无则null）
    target_year  — 目标年份（如"2026"，无则null）
    is_hard_target — 是否量化硬约束（有具体数值+明确目标年份则true）

注意：change_type（新增/调整/延续）不在此步骤产出，将由单独的比对步骤填写。

【变化标注事实（facts）】
  仅收录文档中明确提及的重大政策变化表述（如"删除'稳住楼市股市'表述"）。
  格式：RawClaim — {id, text（≤120字）, summary（≤15字，可加[删除][新增][调整]前缀）}
  一般3条以内。

【总体政策方向结论（conclusions）】
  2-3条对全文政策取向的高层次判断（如"财政政策由稳健转向积极扩张"）。
  格式同 facts：{id, text, summary}
  id 从 100 开始，避免与 facts 的 id 冲突。

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
  若否（广告/纯新闻报道/无政策内容），设 is_relevant_content=false，其余字段留空。

Step B — 提取政策主旨（themes）
  识别3-6个政策大类方向，每个方向含 background + enforcement_note + policies[]。
  background：结合文件内容推断该方向的现实压力与出台动因（"为什么是现在"）。
  policies：每个主旨下2-5条具体政策条目，标注 urgency + metric_value。
  不填写 change_type（由后续比对步骤处理）。

Step C — 提取变化标注事实（facts）
  只收录文档中明确提及的重大政策变化表述。
  注意：量化指标变化不重复列为 fact。

Step D — 提取总体政策方向结论（conclusions）
  2-3条高层次政策取向判断，id 从 100 开始。

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "themes": [
    {{
      "theme_name": "财政政策",
      "background": "外需收缩与内需不足并存，需扩大有效需求",
      "enforcement_note": "财政部牵头，纳入地方绩效考核",
      "has_enforcement_teeth": true,
      "policies": [
        {{
          "summary": "赤字率升至4%",
          "policy_text": "拟按4%左右安排赤字率，赤字规模5.89万亿元",
          "urgency": "mandatory",
          "metric_value": "4%",
          "target_year": "2026",
          "is_hard_target": true
        }}
      ]
    }}
  ],
  "facts": [
    {{"id": 0, "text": "文件提出超常规逆周期调节", "summary": "[新增] 超常规调节"}}
  ],
  "conclusions": [
    {{"id": 100, "text": "财政政策升级为存量增量集成、跨周期与逆周期并重", "summary": "财政集成调控"}}
  ]
}}
```
"""
