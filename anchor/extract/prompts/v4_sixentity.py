"""
Prompt v4_sixentity — 六实体提取（事实/假设/隐含条件/结论/预测/解决方案/关系边）
================================================================================
策略（八步 A-H）：
  Step A：判断相关性
  Step B：提取事实（Fact）— 可独立核查的客观陈述
  Step C：提取假设条件（Assumption）— 作者明确的"如果X则Y"前提
  Step D：提取隐含条件（ImplicitCondition）— 推理依赖但未说出的暗含前提
  Step E：提取结论（Conclusion）— 对过去/当前状态的判断（回顾型）
  Step F：提取预测（Prediction）— 指向未来，须提取时间范围
  Step G：提取解决方案（Solution）— 具体行动建议
  Step H：建立关系边（Relationship）— 六实体间有向边

关键区分：结论 vs 预测
  结论 = 对已发生事件或当前形势的判断（"现在/过去 X 是 Y"）
  预测 = 明确指向未来的判断（"未来 X 将会 Y"，必须有或能推断时间范围）
"""

from __future__ import annotations

from anchor.extract.prompts.base import BasePrompt

_SYSTEM = """\
你是一名专业的内容分析师，擅长从社交媒体帖子中提取结构化论点。
你的任务是识别帖子中的六类实体：事实依据、假设条件、隐含条件、结论（回顾型）、预测（未来型）、解决方案，
并建立它们之间的显式关系边。

核心区分规则：
  【结论 vs 预测】
  - 结论：对过去已发生事件或当前形势的判断，如"当前债务水平已触及临界点"
  - 预测：明确指向未来的判断，如"未来五年美元将贬值"、"2026年将发生经济衰退"
  - 判定原则：含"将"、"会"、"预计"、"到2030年"等时态标志 → 预测；否则 → 结论

  【假设条件 vs 隐含条件】
  - 假设条件：作者明确表述的条件，如"如果利率继续上升"、"假设中国政策不变"
  - 隐含条件：推理中依赖但未说出的前提，如结论"股市下跌"隐含"高利率不利于股票估值"

  【结论 vs 嵌入式未来陈述】
  - 区分作者的"主要预测"与"用来支撑当前结论的未来理由"
  - "起码在可预见的未来X不至于发生Y" → 这是在为当前结论（X现在有护城河）提供支撑，不是独立预测
  - 真正的预测 = 作者将其作为核心前瞻论断单独提出，读者应重点关注其是否实现

  【论证整体性】
  - 多段论证中，最后出现的综合性判断往往是终极结论，反映作者真正的核心立场
  - 若整段文字目的是"解释现象"而非"预测未来"，predictions 应为空

输出必须是合法的 JSON，不加任何其他文字。\
"""

_OUTPUT_SCHEMA = """
请严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{
  "is_relevant_content": true,
  "skip_reason": null,
  "extraction_notes": "提取过程的补充说明（可选）",
  "facts": [
    {
      "summary": "≤15字一句话摘要，高度抽象，如'HALO跑赢M7'",
      "claim": "事实陈述原文（≤120字）",
      "verifiable_statement": "单句可核实陈述，格式：主语+谓语+量化结果+时间，如'2024年Q3美国GDP同比增长2.8%'",
      "temporal_type": "retrospective",
      "temporal_note": "事实有效时间范围，如'2024年Q3'，无则null"
    }
  ],
  "assumptions": [
    {
      "summary": "≤15字一句话摘要，如'AI革命持续深化'",
      "condition_text": "假设条件陈述（≤120字），如'如果美联储在2025年降息50个基点'",
      "verifiable_statement": "可核实表达，或null",
      "temporal_note": "时间范围，或null"
    }
  ],
  "implicit_conditions": [
    {
      "summary": "≤15字一句话摘要，如'重资产抗AI替代'",
      "condition_text": "隐含条件陈述（≤120字），如'高利率环境抑制股票估值'",
      "is_obvious_consensus": false
    }
  ],
  "conclusions": [
    {
      "summary": "≤15字一句话摘要，如'HALO具备护城河'",
      "claim": "结论陈述（回顾型，≤120字），如'当前美国债务水平已危及长期财政可持续性'",
      "verifiable_statement": "单句可核实陈述",
      "author_confidence": "certain|likely|uncertain|speculative"
    }
  ],
  "predictions": [
    {
      "summary": "≤15字一句话摘要，如'美元将贬值20%'",
      "claim": "预测陈述（未来型，≤120字），如'美元将在2027年前贬值20%'",
      "temporal_note": "时间范围，如'2027年前'；无明确时间范围则填null",
      "author_confidence": "certain|likely|uncertain|speculative"
    }
  ],
  "solutions": [
    {
      "summary": "≤15字一句话摘要，如'买入黄金对冲'",
      "claim": "行动建议（≤120字），如'建议持有黄金以对冲美元贬值风险'",
      "action_type": "buy|sell|hold|short|diversify|hedge|reduce|advocate|null",
      "action_target": "行动标的，如'黄金ETF'，或null",
      "action_rationale": "此建议如何从结论/预测推导，或null"
    }
  ],
  "relationships": [
    {
      "source_type": "fact",
      "source_index": 0,
      "target_type": "conclusion",
      "target_index": 0,
      "edge_type": "fact_supports_conclusion",
      "note": "可选说明"
    }
  ]
}
```

edge_type 枚举（仅使用以下值）：
  fact_supports_conclusion          — 事实支撑结论
  assumption_conditions_conclusion  — 假设条件约束结论（"如果X，则结论Y成立"）
  implicit_conditions_conclusion    — 隐含条件支撑结论
  conclusion_supports_conclusion    — 子结论支撑父结论
  conclusion_leads_to_prediction    — 结论推导出预测
  conclusion_enables_solution       — 结论支持解决方案（预测也可使用此类型，source_type填prediction）

注意事项：
1. facts / conclusions / predictions 的数组下标从 0 开始
2. 若内容不相关（如广告、表情包、个人生活），将 is_relevant_content 设为 false
3. 纯粹的未来判断（含时态词）归入 predictions，不要归入 conclusions
4. 解决方案中的 action_type 若不确定请填 null\
"""

_USER_TEMPLATE = """\
## 分析上下文
当前日期：{today}
平台：{platform}
作者：{author}

## 帖子内容
{content}

## 提取任务
请按以下八步分析：

Step A — 相关性判断
  判断此帖子是否包含可分析的观点、事实或预测（非广告、非纯感情帖）。

Step B — 提取事实（Fact）
  识别作者引用的客观陈述，这些陈述原则上可被独立数据源核查。

Step C — 提取假设条件（Assumption）
  识别作者明确表述的"如果X则..."条件性前提。

Step D — 提取隐含条件（ImplicitCondition）
  识别推理链中依赖但未明说的前提假设；若为显而易见的共识（如"水往低处流"），设 is_obvious_consensus=true。

Step E — 提取结论（Conclusion，回顾型）
  识别作者对已发生事件或当前形势的判断。
  【关键区分】：含"将"、"未来"、"会"等时态词 → 通常归入 predictions。
  【例外——嵌入式未来陈述】：如果一个含未来时态的句子是在为当前结论提供支撑理由，而非作者的主要前瞻性断言，则将其保留在结论的 claim 或 verifiable_statement 中，不单独提取为 Prediction。
    判断标准：该句是否可以被移除而不影响作者的核心主张？若移除后核心主张不变 → 是嵌入式理由，归入隐含条件或结论注释；若移除后核心主张消失 → 才是独立预测。
  【终极结论识别】：若文本中存在对整段论证进行综合评判的句子（通常在结尾，形如"因此/综合来看/笔者认为/这意味着……"），应将其标记为最重要的结论，author_confidence 反映作者语气强度。

Step F — 提取预测（Prediction，未来型）
  仅提取作者将其作为**主要前瞻性断言**的陈述，即作者希望读者关注的核心未来判断。
  【不属于 Prediction 的情形】：
    - 作为支撑结论的从属理由（"因为未来X，所以现在Y成立"→ X是隐含条件或结论注释，不是独立预测）
    - 普遍已知的客观规律描述（"长期来看价值投资总会回归"）
    - 作者为增强论点说服力而援引的背景趋势描述
  若无满足条件的预测，predictions 数组留空，不要强行填充。

Step G — 提取解决方案（Solution）
  识别作者给出的具体行动建议（如买入、卖出、持有、规避、倡导等）。

Step H — 建立关系边
  为每个关系建立一条边，使用数组下标引用实体。
  覆盖所有逻辑支撑关系。

{schema}
"""


class PromptV4SixEntity(BasePrompt):
    """六实体提取提示词（v4）"""

    @property
    def version(self) -> str:
        return "v4_sixentity"

    @property
    def system_prompt(self) -> str:
        return _SYSTEM

    def build_user_message(
        self, content: str, platform: str, author: str, today: str | None = None
    ) -> str:
        import datetime
        date_str = today or datetime.date.today().isoformat()
        return _USER_TEMPLATE.format(
            today=date_str,
            platform=platform,
            author=author,
            content=content,
            schema=_OUTPUT_SCHEMA,
        )
