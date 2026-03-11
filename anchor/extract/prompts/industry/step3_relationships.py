"""
Industry Step 3 — 跨层关系边
==============================
建立产业实体间的关系 + 产业实体↔观点实体的跨层关系。

输出 JSON:
{
  "edges": [...]
}
"""

from __future__ import annotations

from typing import List

from anchor.extract.schemas.industry import (
    IndustryContextResult,
    IndustryEntitiesResult,
)

SYSTEM = """\
你是一名专业的产业链结构分析师。任务是为已提取的产业实体建立关系边，包括：
1. 产业实体之间的关系
2. 产业实体与观点实体（fact, conclusion 等）之间的跨层关系

【合法 edge_type 枚举（产业链扩展）】
产业实体间：
  player_dominates_node     — 玩家主导某节点
  player_enters_node        — 玩家进入某节点（新进入者）
  issue_cascades_issue      — 议题级联引发另一议题
  issue_blocks_node         — 议题阻塞某节点
  issue_constrains_player   — 议题约束某玩家
  techroute_mitigates_issue — 技术路线缓解某议题
  techroute_competes_techroute — 技术路线间竞争
  metric_evidences_issue    — 指标数据佐证某议题

产业→观点跨层：
  fact_supports_issue       — 事实支撑某议题
  conclusion_about_player   — 结论涉及某玩家
  conclusion_about_node     — 结论涉及某节点

【source_type / target_type 取值】
  player | supply_node | issue | tech_route | metric | fact | conclusion | theory | prediction

【ID 引用规则】
- 产业实体用 temp_id（如 "p0", "n0", "i0", "t0", "m0"）
- 观点实体用 DB ID 字符串（如 "fact_1", "conclusion_3"）

【注意事项】
- 不能自环
- 只建文中有明确依据的关系，不补推测
- edge_type 必须来自上述枚举

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(
    content: str,
    ctx: IndustryContextResult,
    entities: IndustryEntitiesResult,
    opinion_summary: str,
) -> str:
    # 产业实体概览
    players_lines = [
        f"  [{p.temp_id}] {p.canonical_name}"
        for p in ctx.players
    ]
    nodes_lines = [
        f"  [{n.temp_id}] T{n.tier_id}/{n.layer_name}/{n.node_name}"
        for n in ctx.supply_nodes
    ]
    issues_lines = [
        f"  [{i.temp_id}] {i.summary or i.issue_text[:30]}"
        for i in entities.issues
    ]
    techroutes_lines = [
        f"  [{t.temp_id}] {t.route_name}"
        for t in entities.tech_routes
    ]
    metrics_lines = [
        f"  [{m.temp_id}] {m.metric_name}={m.metric_value}"
        for m in entities.metrics
    ]

    return f"""\
## 产业实体
玩家：
{chr(10).join(players_lines) or "  （无）"}

供应链节点：
{chr(10).join(nodes_lines) or "  （无）"}

议题：
{chr(10).join(issues_lines) or "  （无）"}

技术路线：
{chr(10).join(techroutes_lines) or "  （无）"}

指标：
{chr(10).join(metrics_lines) or "  （无）"}

## 观点实体（已由 v6 提取写入 DB）
{opinion_summary or "（无观点实体）"}

## 文章内容
{content}

## 关系建立任务

为上述实体建立有向关系边：
1. 产业实体之间（如 player_dominates_node, issue_blocks_node）
2. 产业↔观点跨层（如 fact_supports_issue, conclusion_about_player）
3. 只建有文本依据的关系，不补推测

请严格按以下 JSON 格式输出：

```json
{{
  "edges": [
    {{
      "source_type": "player",
      "source_id": "p0",
      "target_type": "supply_node",
      "target_id": "n0",
      "edge_type": "player_dominates_node"
    }},
    {{
      "source_type": "fact",
      "source_id": "fact_1",
      "target_type": "issue",
      "target_id": "i0",
      "edge_type": "fact_supports_issue"
    }}
  ]
}}
```
"""
