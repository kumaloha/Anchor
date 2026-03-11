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

【四层表达模型 — 核心分类框架】

  层 1 — 发生了什么（引用事件/数据/现象）     → Fact
  层 2 — 解释发生了什么（梳理/归纳现状）       → Conclusion（专家解读）
  层 3 — 为什么发生（归因/因果推理）           → Conclusion（必然带 Fact 依据）
  层 4 — 作者的理论框架（建立模型 → 推出预测/行动）→ Theory

【Fact（事实）— 按「来源」标准】
  核心判据：该陈述有外部来源吗？（可以指向一个数据、事件、现象、报道）
  ✓ 有外部来源 → Fact
  ✗ 无外部来源，是作者自己的判断 → 不是 Fact

  包含：
  ✓ 市场数据/表现：「今年S&P500跑赢纳斯达克」「HALO板块今年显著跑赢M7」
  ✓ 行业/公司可观察特征：「重资产行业回报率低」「AI行业投资效率递增」
  ✓ 已发生的事件：「美联储加息25基点」「中国出台新反制裁法」
  ✓ 可观察的现象/趋势：「有色金属需求增加价格上涨」「许多SaaS公司盈利模式受冲击」
  ✗ 不包含：作者的解读/判断（那是 Conclusion）
  ✗ 不包含：作者的理论/框架（那是 Theory）

【Conclusion（结论）— 按「归因」标准】
  核心判据：作者是在解读事实、做出判断吗？
  两种子类型，都是 Conclusion：
  ① 解读型：梳理/归纳现状，体现作者的解读视角
    ✓「HALO是避险交易而非范式转移」（作者的综合判断）
    ✓「部分HALO行业实为AI受益者」（作者的分析归类）
  ② 归因型：「A导致B」— 因果推理
    ✓「地缘紧张推动了大宗商品上涨」（归因，A和B分别作为 Fact 支撑）
    关键：即使 A、B 都是可验证事实，「A导致B」是 Conclusion，A 和 B 分别作为 Fact 支撑。
  关键词：「意味着/说明/因此/体现了/本质上是/笔者认为/导致/源于/归因于」

【Theory（理论框架）— 极度谨慎使用】
  仅当作者明确提出一个有名字的理论/模型/原则时才归为 Theory。
  ✓ 「大周期理论：帝国兴衰遵循约80年的债务-政治-地缘三重周期」
  ✓ 「AI 赋能三阶段模型：基建投入→应用爆发→产业重塑」
  ✓ 「核心原则：在债务周期晚期应持有实物资产而非金融资产」
  ✗ 「财政破产+贫富差距=内战先导」→ 这是 Conclusion（历史规律总结，不是理论框架）
  ✗ 「有毒组合三要素：高债务、大差距、经济冲击」→ 这是 Conclusion（对现状的归纳判断）
  ✗ 「第5阶段的典型特征」→ 这是 Conclusion（将理论应用于现实的判断）

  Theory vs Conclusion 的关键区别：
  - Theory = 作者建立的**顶层分析框架**，有明确名称，一篇文章通常只有 0-2 个
  - Conclusion = 作者运用理论得出的**具体判断**，即使涉及规律/模式也是 Conclusion
  - 拿不准时，归为 Conclusion（宁多 Conclusion 不多 Theory）

  DAG 位置：Theory 在最上游，与 Fact 一起作为论证起点

【Prediction（预测）】
  明确指向未来，含「将/会/预计/到X年」等时态标志词，
  且是作者的主要前瞻性断言（而非为当前结论提供从属支撑的背景理由）。

【Assumption（假设）】
  作者明确标出的条件性前提：「如果X则...」「假设X成立」。

【Solution（解决方案）】
  作者给出的具体行动建议，含「买入/卖出/持有/建议/对冲」等行动动词。

  DAG 位置辅助判断：
    在 DAG 中没有入边（源节点）→ 通常是 Fact（论证的起点）
    在 DAG 中有入边和出边  → 通常是中间 Conclusion 或 Theory
    在 DAG 中只有入边（★核心结论）→ 通常是终极 Conclusion

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
- entity_type：fact | conclusion | prediction | assumption | solution | theory
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
