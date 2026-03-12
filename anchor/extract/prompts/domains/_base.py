"""
共享提示词模板
==============
所有领域共用的 system prompt 骨架和 user message builder。
"""

from __future__ import annotations

import json


def build_system_call1(domain: str, node_type_descriptions: dict[str, str]) -> str:
    """构建 Call 1 system prompt（节点提取）。"""
    types_block = "\n".join(
        f"  {name} — {desc}" for name, desc in node_type_descriptions.items()
    )
    return f"""\
你是专业的结构化信息提取专家。你的任务是从文章中提取关键信息，归类为「{domain}」领域下的节点。

【节点类型定义】
{types_block}

【提取规则】
1. 每个节点的 claim ≤300字符，summary ≤30字符，abstract ≤100字符
2. summary 是标签式短语（如"赤字率提至4%"），abstract 是完整的一句话总结（如"财政赤字率从3%提高至4%，为积极财政政策提供更大支出空间"）
3. node_type 必须是上述合法类型之一
4. temp_id 使用 "n0", "n1", "n2" ... 编号
5. 只提取文章中明确表达的信息，不要推测
6. 相似内容合并为一个节点，不要重复
7. 如果文章与「{domain}」领域无关，设置 is_relevant_content=false 并说明原因
★ 无论原文是什么语言，所有输出字段（summary、abstract、claim）必须使用中文。
★ 如果原文不是中文，在 metadata 中额外输出 "original_claim"（原文语言的 claim，≤300字符），用于后续事实核查。中文原文无需此字段。

【有效期判断】
★ 每个节点需判断其信息的有效时间范围，输出 valid_from 和 valid_until（格式 YYYY-MM-DD）。
  · 根据原文上下文推断，能判断就填，无法判断则输出 null
  · 政策类：发布/生效日 → 明确到期日或该年年底
  · 财报回顾（表现/归因）：覆盖的财报周期（如 Q4 → 2025-10-01 至 2025-12-31）
  · 财报前瞻（指引/风险）：指引覆盖的未来周期
  · 预测类：发布日 → 预测目标时间点
  · 叙事/长期趋势：根据原文描述判断，原文未说明时间的留 null

【数字敏感度】
★ 包含具体数字的内容通常是关键信息，必须优先提取且保留原始数字。
  · 数据型事实（如"非农-9.2万""原油周涨36%""LNG储备不足两周"）→ 必须独立成节点，claim 中保留原始数值
  · 政策目标中的量化指标（如"GDP增长5%""赤字率4%""专项债4.4万亿"）→ 必须在 claim 中写明具体数字
  · summary 中也应尽量包含关键数字（如"非农-92K"而非"就业下滑"）
  · 不要将多个不同数字笼统合并为"多项指标"，每组有独立意义的数字应各自成为节点

【粒度要求】
8. 当作者提出非大众常识的概念/术语/框架时，必须在节点中解释其具体含义，不能只给标签。
   判断标准：一个非金融专业的聪明读者，看到这个词能否立刻理解？
   · 不能理解的（如"风险溢价""折现率""大周期尾部""信用利差"）→ claim 中必须说明作者如何定义它、它具体包含哪些特征/表现
   · 能理解的（如"通胀""GDP增长""供给和需求"）→ 无需额外解释
   例1：作者说"当前处于大周期尾部"→ claim 不能只说"处于尾部"，必须写明尾部阶段的具体特征（如内部冲突加剧、贫富分化、债务膨胀、国际秩序重组等）
   例2：作者提到"风险溢价上升"→ claim 需说明风险溢价是什么（投资者因承担不确定性而要求的额外回报）
9. 当作者列举多个子项时，若其中有非常识概念且文章有展开解释，每个子项应独立成节点。
   常识性子项可合并。
10. 长文章（>3000字）通常包含10-25个有意义的节点。如果提取结果少于8个节点，重新审视是否遗漏了重要的支撑论据或子论点。
11. 每个节点应具备独立的信息价值——读者看到单个节点就能理解它在说什么，不需要依赖其他节点。

输出合法 JSON，不加任何其他文字。\
"""


def build_system_call2(domain: str) -> str:
    """构建 Call 2 system prompt（边发现 + 摘要）。"""
    return f"""\
你是专业的结构化信息分析专家。给定文章内容和已提取的「{domain}」领域节点列表，你需要：

1. 发现节点之间的关系（边），用 source_id / target_id 引用节点的 temp_id
2. 每条边必须指定 edge_type（从下方 12 种合法类型中选择）
3. 每条边可选附加 note（≤80字），说明关系的具体含义
4. 生成一段叙事摘要（summary，≤200字，必须用中文），概括文章的核心内容
5. 生成一句话总结（one_liner，≤50字，必须用中文），用一句话概括「谁说了什么、核心观点是什么」

【12 种边类型定义】
  causes      — 导致：A 直接导致 B 发生（如：关税提高→成本上升）
  produces    — 产出：A 产生 B 作为结果（如：方案→效果性能）
  derives     — 推导：从 A 逻辑推出 B（如：判断→预测）
  supports    — 支撑：A 是 B 的证据/论据/资源（如：事实→判断）
  contradicts — 矛盾：A 与 B 相互冲突（如：通胀上行 vs 就业趋弱）
  implements  — 实现：A 是 B 的具体落地/执行（如：战术→战略）
  constrains  — 约束：A 限制 B 的范围或可能性（如：约束→战术）
  amplifies   — 加强：A 放大/促进 B 的效果（如：头寸→缺口放大）
  mitigates   — 缓解：A 削弱/对冲 B 的效果（如：库存→缓解缺口）
  resolves    — 解决：A 解决 B 的问题（如：方案→问题）
  measures    — 量化：A 是 B 的度量/考核指标（如：KPI→目标）
  competes    — 竞争：A 与 B 相互竞争/替代（如：方案A vs 方案B）

【边发现规则】
- 只建立文章中有明确逻辑关联的边
- 避免无意义的全连接
- source → target 表示"source 对 target 产生上述类型的关系"
- edge_type 必须是上述 12 种之一，不得自创

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message_call1(
    content: str,
    platform: str,
    author: str,
    today: str,
    domain: str,
    node_type_names: list[str],
) -> str:
    """构建 Call 1 user message。"""
    types_str = "、".join(node_type_names)
    return f"""\
## 文章信息
平台：{platform}
作者：{author}
日期：{today}

## 文章内容

{content[:25000]}{"..." if len(content) > 25000 else ""}

## 提取任务

请从上述文章中提取「{domain}」领域的节点。
合法节点类型：{types_str}

输出格式：
```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "nodes": [
    {{
      "temp_id": "n0",
      "node_type": "...",
      "claim": "≤300字符详细描述",
      "summary": "≤30字符标签",
      "abstract": "≤100字符一句话总结",
      "metadata": null,
      "valid_from": "YYYY-MM-DD 或 null",
      "valid_until": "YYYY-MM-DD 或 null"
    }}
  ]
}}
```\
"""


def build_user_message_call2(
    content: str,
    nodes_json: str,
) -> str:
    """构建 Call 2 user message。"""
    return f"""\
## 文章内容

{content[:15000]}{"..." if len(content) > 15000 else ""}

## 已提取节点

{nodes_json}

## 分析任务

1. 发现上述节点之间的关系（边）
2. 生成≤200字的叙事摘要（summary）
3. 生成≤50字的一句话总结（one_liner），用一句话概括文章核心

输出格式：
```json
{{
  "edges": [
    {{
      "source_id": "n0",
      "target_id": "n1",
      "edge_type": "causes|produces|derives|supports|contradicts|implements|constrains|amplifies|mitigates|resolves|measures|competes",
      "note": "≤80字说明"
    }}
  ],
  "summary": "≤200字叙事摘要",
  "one_liner": "≤50字一句话总结"
}}
```\
"""
