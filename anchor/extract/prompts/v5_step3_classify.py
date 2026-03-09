"""
v5 Step 3 — 节点分类
======================
目标：基于全局 DAG 上下文对每个声明节点进行六实体分类。

输入：全量声明（含合并后摘要）+ 边列表 + 核心结论节点 id 列表
输出 JSON：
{
  "classifications": [
    {
      "claim_id": 0,
      "entity_type": "fact",
      "verifiable_statement": "..."
    }
  ]
}
"""

from __future__ import annotations

from typing import List, Set

from anchor.extract.schemas import RawClaim, RawEdge

SYSTEM = """\
你是一名专业的内容分析师，擅长在论证 DAG（有向无环图）上下文中对声明进行精确分类。
verifiable_statement 字段必须使用中文。

【核心分类原则：作者是在"引用现实"还是在"做出判断"？】

  Fact  = 作者将其作为论证的起点，呈现为「世界上已经发生的事/已经存在的状态」
  Conclusion = 作者基于 Fact 推导出的分析判断，是作者自己的解读

  判断方法：
    问「这个陈述是作者的论证依据，还是作者的论证结果？」
    → 是依据（作者在引用事实来支撑观点）→ Fact
    → 是结果（作者在解读事实、得出判断）→ Conclusion

  DAG 位置辅助判断：
    在 DAG 中没有入边（源节点）→ 通常是 Fact（论证的起点）
    在 DAG 中有入边和出边  → 通常是中间 Conclusion
    在 DAG 中只有入边（★核心结论）→ 通常是终极 Conclusion

【Fact（事实）— 更宽的定义】
  包含所有作者以"证据"身份引用的陈述，不要求必须有精确数据或第三方核查：
  ✓ 市场表现：「今年S&P500跑赢纳斯达克」「HALO板块今年显著跑赢M7」
  ✓ 行业/公司特征：「重资产行业回报率低且投资效率递减」「AI行业具投资效率递增优势」
  ✓ 市场/行业趋势：「AI公司在基础设施领域持续大额投资且回报不确定」「有色金属需求增加价格上涨」
  ✓ 外部条件：「地缘政治不确定性推动大国战略储备有色金属」
  ✓ 可观察的商业现象：「许多SaaS软件公司盈利模式受到冲击」「重资产行业估值便宜」
  ✗ 不包含：作者的分析判断（那是 Conclusion）

【Conclusion（结论）】
  作者基于事实推导出的分析性判断，体现作者的解读视角：
  ✓「HALO是避险交易而非范式转移」（作者对多个事实的综合判断）
  ✓「AI主要对实体经济是赋能而非摧毁」（作者的解读）
  ✓「部分HALO行业实为AI受益者」（作者的分析归类）
  关键词：「意味着/说明/因此/体现了/本质上是/笔者认为/在这个意义上」

【Prediction（预测）】
  明确指向未来，含「将/会/预计/到X年」等时态标志词，
  且是作者的主要前瞻性断言（而非为当前结论提供从属支撑的背景理由）。

【Assumption（假设）】
  作者明确标出的条件性前提：「如果X则...」「假设X成立」。

【Solution（解决方案）】
  作者给出的具体行动建议，含「买入/卖出/持有/建议/对冲」等行动动词。

输出合法 JSON，不加任何其他文字。\
"""


def build_user_message(
    claims: List[RawClaim],
    edges: List[RawEdge],
    core_ids: Set[int],
    isolated_ids: Set[int],
) -> str:
    # 构建 DAG 文本表示
    dag_lines = []
    edge_map: dict[int, list[int]] = {}
    for e in edges:
        edge_map.setdefault(e.from_id, []).append(e.to_id)

    claims_by_id = {c.id: c for c in claims}

    # 节点列表
    nodes_text_parts = []
    for c in claims:
        tag = ""
        if c.id in core_ids:
            tag = " [★核心结论]"
        elif c.id in isolated_ids:
            tag = " [孤立节点]"
        nodes_text_parts.append(f"  [{c.id}]{tag} {c.summary}：{c.text}")
    nodes_text = "\n".join(nodes_text_parts)

    # 边列表（文本化）
    if edges:
        edges_text_parts = []
        for e in edges:
            src = claims_by_id.get(e.from_id)
            tgt = claims_by_id.get(e.to_id)
            src_label = src.summary if src else str(e.from_id)
            tgt_label = tgt.summary if tgt else str(e.to_id)
            edges_text_parts.append(f"  [{e.from_id}]{src_label} → [{e.to_id}]{tgt_label}")
        edges_text = "\n".join(edges_text_parts)
    else:
        edges_text = "  （无边，所有节点孤立）"

    classifications_template = ",\n    ".join(
        f'{{"claim_id": {c.id}, "entity_type": "?", "verifiable_statement": null}}'
        for c in claims
    )

    return f"""\
## DAG 结构

### 节点（共 {len(claims)} 个）
{nodes_text}

### 有向边（共 {len(edges)} 条，方向 = 前提 → 结论）
{edges_text}

## 分类任务

根据上述 DAG 全局视角，对每个节点进行实体分类。
- entity_type：fact | conclusion | prediction | assumption | solution
- verifiable_statement：仅对 fact 和 conclusion 填写（单句可核实陈述，格式：主语+谓语+量化结果+时间）；其他类型填 null
- author_confidence：仅对 conclusion 和 prediction 填写（certain|likely|uncertain|speculative）；其他类型填 null
- temporal_note：仅对 prediction 填写时间范围（如"2027年前"）；无明确时间范围填 null
- action_type：仅对 solution 填写（buy|sell|hold|short|diversify|hedge|reduce|advocate|null）
- action_target：仅对 solution 填写行动标的；其他类型填 null
- action_rationale：仅对 solution 填写推导依据；其他类型填 null

请严格按以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "classifications": [
    {classifications_template}
  ]
}}
```
"""
