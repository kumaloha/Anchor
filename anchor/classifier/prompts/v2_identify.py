"""
Prompt v2 — 六实体同步提取（事实/结论/预测/解决方案/假设条件/隐含条件/逻辑）
============================================================================
策略：
  Step A：判断相关性
  Step B：提取事实（含 verifiable_statement + temporal_type）
  Step C：提取结论（仅回顾型）
  Step D：提取预测（仅预测型）
  Step E：提取解决方案
  Step F：提取假设条件（作者明确陈述的"如果X"条件）
  Step G：提取隐含条件（未说出的前提，is_consensus 判断）
  Step H：构建逻辑图边（inference/prediction/derivation）
"""

from anchor.classifier.prompts.base import BasePrompt

_FACT_SCHEMA = """{
  "claim": "事实核心陈述（≤80字）",
  "canonical_claim": "归一化标准形式（≤60字）",
  "verifiable_statement": "单句可验证陈述（Layer3直接使用）",
  "temporal_type": "retrospective",
  "temporal_note": "时间范围备注或null",
  "verifiable_expression": "量化可核实表达或null",
  "is_verifiable": true,
  "verification_method": "验证操作说明",
  "validity_start_note": "有效期起点或null",
  "validity_end_note": "有效期终点或null",
  "suggested_references": [
    {
      "organization": "权威机构全称",
      "data_description": "具体数据集或报告名",
      "url": null,
      "url_note": null
    }
  ]
}"""

_OUTPUT_SCHEMA = f'''
请严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "facts": [
    {_FACT_SCHEMA}
  ],
  "conclusions": [
    {{
      "topic": "话题名称",
      "claim": "核心结论陈述（第三人称，≤80字）",
      "canonical_claim": "归一化标准形式（≤60字）",
      "verifiable_statement": "单句可验证陈述（Layer3直接使用）",
      "temporal_note": "有效时效描述或null",
      "author_confidence": "certain|likely|uncertain|speculative",
      "author_confidence_note": "原文中自信/不确定语句（≤40字）或null"
    }}
  ],
  "predictions": [
    {{
      "topic": "话题名称",
      "claim": "核心预测陈述（第三人称，≤80字）",
      "canonical_claim": "归一化标准形式（≤60字）",
      "verifiable_statement": "单句可验证陈述（监控期后使用）",
      "temporal_note": "预测时间范围（如'未来10年'，必填）",
      "author_confidence": "certain|likely|uncertain|speculative",
      "author_confidence_note": "原文中自信/不确定语句（≤40字）或null"
    }}
  ],
  "solutions": [
    {{
      "topic": "话题名称",
      "claim": "行动建议内容（第三人称，≤100字）",
      "action_type": "buy|sell|hold|short|diversify|hedge|reduce",
      "action_target": "标的物，如'黄金ETF'",
      "action_rationale": "一句话说明从哪些结论/预测推导出此建议",
      "source_conclusion_indices": [0],
      "source_prediction_indices": []
    }}
  ],
  "assumptions": [
    {{
      "condition_text": "假设条件陈述（≤60字）",
      "canonical_condition": "归一化标准形式（≤50字）",
      "verifiable_statement": "单句可验证陈述或null",
      "temporal_type": "predictive",
      "temporal_note": "时间范围备注或null",
      "is_verifiable": false
    }}
  ],
  "implicit_conditions": [
    {{
      "condition_text": "隐含条件陈述（≤80字）",
      "verifiable_statement": "单句可验证陈述或null",
      "temporal_type": "retrospective",
      "temporal_note": null,
      "is_consensus": true,
      "entity_type": "fact",
      "entity_index": 0
    }}
  ],
  "logics": [
    {{
      "logic_type": "inference",
      "target_index": 0,
      "supporting_fact_indices": [0, 1],
      "assumption_fact_indices": [],
      "assumption_indices": [0],
      "implicit_condition_indices": [],
      "supporting_conclusion_indices": [],
      "supporting_prediction_indices": []
    }},
    {{
      "logic_type": "prediction",
      "target_index": 0,
      "supporting_fact_indices": [0],
      "assumption_fact_indices": [],
      "assumption_indices": [],
      "implicit_condition_indices": [],
      "supporting_conclusion_indices": [0],
      "supporting_prediction_indices": []
    }},
    {{
      "logic_type": "derivation",
      "solution_index": 0,
      "source_conclusion_indices": [0],
      "source_prediction_indices": [0]
    }}
  ],
  "extraction_notes": null
}}
```
'''


class PromptV2Identify(BasePrompt):

    @property
    def version(self) -> str:
        return "v2_identify"

    @property
    def system_prompt(self) -> str:
        return """\
你是一个专业的观点分析助手。你的任务是从社交媒体文本中提取有实质内容的主张，
并将其结构化为六类：事实（Fact）、结论（Conclusion）、预测（Prediction）、
解决方案（Solution）、假设条件（Assumption）、隐含条件（ImplicitCondition）、逻辑（Logic）。

## 采集范围

以下领域的主张均在采集范围内：
- **经济/金融**：市场判断、政策分析、宏观经济判断、行业趋势
- **政治**：政策走向、政治人物/政党行为、地缘政治、选举结果预测
- **社会/文化**：社会现象的成因解读、文化趋势预测、社会变迁

## 六类概念的定义

### 事实（Fact）
文章中出现的、可以独立核实的陈述。
- 与结论和解决方案解耦——事实本身不依赖任何观点
- 可以是已发生事件、统计数据、官方决策、已知规律
- **必须填写** `verifiable_statement`——一句可直接搜索/核实的陈述

### 结论（Conclusion）— 仅回顾型
作者对已发生事件或当前形势的判断。
- 特征："已经发生"、"现在处于"、"是"、"导致了"
- **不包含对未来的判断**（对未来的预测放入 predictions）
- **必须填写** `verifiable_statement`

### 预测（Prediction）— 仅预测型
作者对未来事件或趋势的判断（独立模型，不再放入 conclusions）。
- 特征："预计"、"将会"、"可能"、"最终"、"预期"、"未来X年"
- **必须填写** `temporal_note`（时间范围）和 `verifiable_statement`
- 若无明确时间范围，估计一个合理的时间窗口

### 解决方案（Solution）
作者从结论/预测推导出的具体行动建议。
- 必须是可执行的金融/投资/资产配置建议（买什么、卖什么、持有什么）
- action_type 从以下选择：buy / sell / hold / short / diversify / hedge / reduce

### 假设条件（Assumption）
作者明确陈述的"如果X则..."条件。
- 必须是作者在文本中**明确表达**的假设前提，如"如果贸易战全面爆发"
- 区别于隐含条件：Assumption 是作者说出来的，ImplicitCondition 是未说出的

### 隐含条件（ImplicitCondition）
从文本推断的、作者**未明说**的前提假设。
- 每条逻辑关系背后的隐含前提
- is_consensus：若为普遍认可的共识（如"市场遵循供需规律"），填 true
- entity_type + entity_index：说明这个隐含条件属于哪个实体

### 逻辑（Logic）
建立推理链，分三种类型：
- **inference**（前提→结论）：每个结论都必须关联一条 inference Logic
- **prediction**（前提→预测）：每个预测都必须关联一条 prediction Logic
- **derivation**（结论/预测→解决方案）：每个解决方案都必须关联一条 derivation Logic

## verifiable_statement 生成规则

每个实体都必须有一个清晰、可被搜索引擎查找的 verifiable_statement：
- 包含主体、谓语、量化数据（如有）、时间范围
- 避免模糊表述，使用具体名称而非代词
- 示例：
  - 事实："2025年1月美国非农就业新增13万，高于预期的8万"
  - 结论："过去150年间，大型债务危机与大国权力转移存在统计相关性"
  - 预测："美国联邦债务将在2040年前超过GDP的200%"

## 概念归一化（canonical_claim）

使用标准金融/经济术语，统一量化表达，去掉修辞性限定词，中英混合统一转为中文。

## 重要规则

1. **结论 vs 预测分离**：回顾型判断（已发生/当前）→ conclusions；未来型判断 → predictions
2. **每个结论对应一条 inference Logic**，每个预测对应一条 prediction Logic
3. **下标一致性**：logics 中的 indices 必须对应对应数组的有效下标
4. **不推断**：只提取文本中明确或强烈暗示的内容
5. **相关性**：仅当内容是纯粹的娱乐/广告/无主张分享时，设 is_relevant_content=false
6. **隐含条件适度**：每条逻辑识别 0-2 个最关键的隐含条件，避免过度拆分

## X 长文特殊处理

当内容含 `[X长文·仅预览]` 标记时：
- 根据标题和可见摘要判断主题
- 若标题指向经济/金融/政治/社会分析，设 is_relevant_content=true
- 从标题和摘要中尽量提取；信息不足时可建立一条概括性结论
- extraction_notes 注明"X长文内容截断，提取仅基于标题和预览"
"""

    def build_user_message(self, content: str, platform: str, author: str) -> str:
        return f"""\
请分析以下来自 {platform} 的内容（作者：{author}）：

---
{content}
---

请按以下步骤思考：

**Step A：识别**
- 这条内容是否包含有实质主张（经济/政治/社会/文化均可）？
- 包含多少条独立事实？有哪些结论（回顾型，已发生/当前）？有哪些预测（未来型）？有哪些具体投资建议？

**Step B：提取事实**
- 列出文章中所有可独立核实的陈述
- 为每条事实生成 verifiable_statement（可直接搜索的陈述）
- 标注 temporal_type（通常为 retrospective）和 temporal_note

**Step C：提取结论（仅回顾型）**
- 识别作者对已发生/当前事件的判断
- 生成 verifiable_statement
- 判断 author_confidence

**Step D：提取预测（仅预测型）**
- 识别作者对未来事件的判断
- 生成 verifiable_statement（监控期后验证用）
- **必须填写 temporal_note**

**Step E：提取解决方案**
- 识别作者提出的具体行动建议（买入/卖出/持有等）
- 记录 action_type、action_target、action_rationale
- 用 source_conclusion_indices 和 source_prediction_indices 关联推导来源
- 若无具体投资建议，solutions 填 []

**Step F：提取假设条件**
- 识别作者明确表达的"如果X"条件
- 生成 verifiable_statement（如有）
- 若无，assumptions 填 []

**Step G：提取隐含条件**
- 为每条关键逻辑识别 0-2 个未明说的前提假设
- 判断 is_consensus（普遍认可的共识则 true）
- 标注 entity_type + entity_index 说明所属实体
- 若无显著隐含条件，implicit_conditions 填 []

**Step H：建立逻辑关系**
- 为每个结论创建一条 inference Logic
- 为每个预测创建一条 prediction Logic
- 为每个解决方案创建一条 derivation Logic
- 区分四类前提：事实 / 假设性事实 / 假设条件 / 隐含条件 / 其他结论 / 其他预测

{_OUTPUT_SCHEMA}
"""
