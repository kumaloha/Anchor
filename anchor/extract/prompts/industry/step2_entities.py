"""
Industry Step 2 — Issue + TechRoute + Metric
==============================================
基于 Step 1 结果，提取产业议题、技术路线和指标数据。

输出 JSON:
{
  "issues": [...],
  "tech_routes": [...],
  "metrics": [...]
}
"""

from __future__ import annotations

import json
from typing import List

from anchor.extract.schemas.industry import (
    ExtractedPlayer,
    ExtractedSupplyNode,
    IndustryContextResult,
)

SYSTEM = """\
你是一名专业的产业链分析师。任务是从文章中提取产业议题（Issue）、技术路线（TechRoute）和量化指标（Metric）。

【语言要求】
所有输出字段使用中文（metric_name 和 unit 可用通用英文缩写如 TOPS, nm, GW）。

【产业议题 issues】
文中讨论的产业瓶颈、风险、争议或机遇。

规则：
1. issue_text ≤150字，明确描述该议题
2. severity: critical（可能引发产业链断裂）| high | medium | low
3. status: active（当前存在）| emerging（初现端倪）| resolved
4. supply_node_ref: 关联到 Step 1 中的 SupplyNode temp_id（如 "n0"），无关联则 null
5. temp_id 格式："i0", "i1", ...

【技术路线 tech_routes】
文中讨论的技术方案/路径。

规则：
1. route_name: 技术路线名称
2. maturity: experimental | emerging | growth | mature | declining
3. competing_routes: 该路线的竞争替代方案名称列表
4. supply_node_ref: 关联到 SupplyNode temp_id
5. temp_id 格式："t0", "t1", ...

【量化指标 metrics】
文中出现的具体数值数据。

规则：
1. metric_name: 指标名称（如 "算力总量", "市占率"）
2. metric_value: 数值（字符串形式，如 "72%", "1.3万亿"）
3. unit: 单位（如 "%", "TOPS", "亿美元"）
4. time_reference: 时间参考（如 "2025Q4", "2026年"）
5. player_ref: 关联 Player temp_id（如 "p0"），无关联则 null
6. supply_node_ref: 关联 SupplyNode temp_id（如 "n0"），无关联则 null
7. evidence_score: 0-1（1=有明确出处，0.5=行业共识无来源，0=推测）
8. temp_id 格式："m0", "m1", ...

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(
    content: str, ctx: IndustryContextResult,
) -> str:
    players_summary = "\n".join(
        f"  [{p.temp_id}] {p.canonical_name} ({p.entity_type or '?'})"
        for p in ctx.players
    )
    nodes_summary = "\n".join(
        f"  [{n.temp_id}] T{n.tier_id}/{n.layer_name}/{n.node_name}"
        for n in ctx.supply_nodes
    )

    return f"""\
## 产业上下文（Step 1 结果）
产业链：{ctx.industry_chain}
涉及层级：{ctx.tiers_covered}

玩家列表：
{players_summary or "  （无）"}

供应链节点：
{nodes_summary or "  （无）"}

## 文章内容
{content}

## 提取任务

Task A — 提取产业议题
  文中讨论了哪些瓶颈/风险/争议/机遇？

Task B — 提取技术路线
  文中讨论了哪些技术方案？是否有替代/竞争路线？

Task C — 提取量化指标
  文中出现了哪些具体数值？关联到哪个玩家/节点？

请严格按以下 JSON 格式输出：

```json
{{
  "issues": [
    {{
      "temp_id": "i0",
      "supply_node_ref": "n0",
      "issue_text": "议题描述（≤150字）",
      "severity": "high",
      "status": "active",
      "resolution_progress": "进展描述（≤80字）",
      "summary": "≤15字摘要"
    }}
  ],
  "tech_routes": [
    {{
      "temp_id": "t0",
      "supply_node_ref": "n0",
      "route_name": "CoWoS 封装",
      "maturity": "growth",
      "competing_routes": ["InFO_oS", "EMIB"],
      "summary": "≤15字摘要"
    }}
  ],
  "metrics": [
    {{
      "temp_id": "m0",
      "supply_node_ref": "n0",
      "player_ref": "p0",
      "metric_name": "市占率",
      "metric_value": "72%",
      "unit": "%",
      "time_reference": "2025Q4",
      "evidence_score": 0.8
    }}
  ]
}}
```
"""
