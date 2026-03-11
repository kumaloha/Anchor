"""
Industry Step 1 — 产业上下文 + Player + SupplyNode
====================================================
识别文章所属产业链，提取涉及的产业玩家和供应链节点。

输出 JSON:
{
  "industry_chain": "AI",
  "tiers_covered": [1, 3, 5],
  "players": [...],
  "supply_nodes": [...]
}
"""

from __future__ import annotations

SYSTEM = """\
你是一名专业的产业链分析师。任务是从产业研究文章中识别产业上下文、关键玩家和供应链节点。

【语言要求】
所有输出字段使用中文，但 canonical_name（公司/机构名）保留其最通用的名称形式（英文公司用英文，中文机构用中文）。

【产业链 industry_chain】
用 ≤4 字概括，如："AI"、"新能源车"、"半导体"、"光伏"。

【产业玩家 players】
提取文中明确提及的企业/机构/联盟。

规则：
1. canonical_name 使用最广为人知的名称（如 NVIDIA 而非 英伟达 Inc.）
2. aliases 包含文中出现的所有其他称呼（中英文别名、简称）
3. entity_type: company|government|research_institute|alliance|startup
4. temp_id 格式："p0", "p1", ...
5. 仅提取文中明确提到的玩家，不补推测

【供应链节点 supply_nodes】
识别文中涉及的产业链环节。

规则：
1. tier_id 为层级编号（从上游到下游递增，如：1=基础材料, 2=芯片设计, 3=制造, ...）
2. layer_name 为该层的通用名称
3. node_name 为该层下的具体节点
4. 同一 tier_id 下可有多个不同 node_name
5. temp_id 格式："n0", "n1", ...

输出合法 JSON，不加任何其他文字.\
"""


def build_user_message(
    content: str, platform: str, author: str, today: str,
) -> str:
    return f"""\
## 分析上下文
当前日期：{today}
平台：{platform}
作者：{author}

## 文章内容
{content}

## 提取任务

Task A — 识别产业链
  该文章研究的是哪条产业链？用 ≤4 字概括。

Task B — 识别层级覆盖
  文章涉及了哪些层级（tier_id 列表）？

Task C — 提取产业玩家
  提取文中明确提及的企业/机构。每个玩家给出 canonical_name + aliases。

Task D — 提取供应链节点
  识别文中涉及的产业链环节，按 tier_id 组织。

请严格按以下 JSON 格式输出：

```json
{{
  "industry_chain": "AI",
  "tiers_covered": [1, 3, 5],
  "players": [
    {{
      "temp_id": "p0",
      "name": "文中出现的名称",
      "canonical_name": "NVIDIA",
      "aliases": ["英伟达", "Nvidia"],
      "entity_type": "company",
      "headquarters": "美国"
    }}
  ],
  "supply_nodes": [
    {{
      "temp_id": "n0",
      "tier_id": 1,
      "layer_name": "算力芯片",
      "node_name": "GPU 设计",
      "description": "高性能 GPU 架构设计"
    }}
  ]
}}
```
"""
