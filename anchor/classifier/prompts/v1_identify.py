"""
Prompt v1 — 四类观点提取（事实/结论/解决方案/逻辑）
=================================================
策略：
  Step A：判断相关性，识别文中包含哪些类型的主张
  Step B：提取事实（独立陈述）
  Step C：提取结论（含回顾型 retrospective 和预测型 predictive）
  Step D：提取解决方案（作者提出的具体行动建议）
  Step E：建立逻辑关系
    - inference：哪些事实支撑哪个结论，哪些是假设条件
    - derivation：哪些结论推导出哪个解决方案
"""

from anchor.classifier.prompts.base import BasePrompt

_FACT_SCHEMA = """
        {
          "claim": "事实的核心陈述（≤80字，保留原意）",
          "canonical_claim": "概念归一化标准形式（≤60字，标准术语，跨帖匹配用）",
          "verifiable_expression": "含量化指标和时间范围的可核实表达，或null",
          "is_verifiable": true,
          "verification_method": "具体验证操作：对比哪段时间的哪个数据，判定阈值是什么",
          "validity_start_note": "事实有效期起点或null",
          "validity_end_note": "事实有效期终点或null",
          "suggested_references": [
            {
              "organization": "权威机构全称",
              "data_description": "具体数据集或报告名",
              "url": "数据页面URL（可确定时填写，不确定则null）",
              "url_note": "URL使用说明或null"
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
      "canonical_claim": "概念归一化标准形式（≤60字，标准术语）",
      "conclusion_type": "retrospective",
      "time_horizon_note": "结论有效时效描述或null",
      "valid_until_note": "仅predictive时填写预测有效期，否则null"
    }}
  ],
  "solutions": [
    {{
      "topic": "话题名称",
      "claim": "行动建议内容（第三人称，≤100字）",
      "action_type": "buy|sell|hold|short|diversify|hedge|reduce",
      "action_target": "标的物，如'黄金ETF'",
      "action_rationale": "一句话说明从哪些结论推导出此建议",
      "source_conclusion_indices": [0, 1]
    }}
  ],
  "logics": [
    {{
      "logic_type": "inference",
      "target_index": 0,
      "supporting_fact_indices": [0, 1],
      "assumption_fact_indices": []
    }},
    {{
      "logic_type": "derivation",
      "solution_index": 0,
      "source_conclusion_indices": [0, 1]
    }}
  ],
  "extraction_notes": null
}}
```
'''


class PromptV1Identify(BasePrompt):

    @property
    def version(self) -> str:
        return "v1_identify"

    @property
    def system_prompt(self) -> str:
        return """\
你是一个专业的观点分析助手。你的任务是从社交媒体文本中提取有实质内容的主张，
并将其结构化为四类：事实（Fact）、结论（Conclusion）、解决方案（Solution）、逻辑（Logic）。

## 采集范围

以下领域的主张均在采集范围内：
- **经济/金融**：市场判断、政策分析、宏观经济判断、行业趋势
- **政治**：政策走向、政治人物/政党行为、地缘政治、选举结果预测
- **社会/文化**：社会现象的成因解读、文化趋势预测、社会变迁

## 四类概念的定义

### 事实（Fact）
文章中出现的、可以独立核实的陈述。
- 与结论和解决方案解耦——事实本身不依赖任何观点
- 可以是已发生事件、统计数据、官方决策、已知规律
- 必须能被权威来源验证（或明确标注无法验证）
- **提取原则**：
  - 一条事实对应一个可独立核实的陈述
  - 先列出文章中所有相关事实，再建立逻辑关系
  - 每条可验证的事实必须填写 verification_method 和 suggested_references

### 结论（Conclusion）
作者的分析判断，分两种类型：
- **回顾型（retrospective）**：对过去或当前事件的判断
  - 特征："已经发生"、"现在处于"、"是"、"导致了"
  - 示例："过去150年间大周期循环导致财富更替"
- **预测型（predictive）**：对未来事件或趋势的判断
  - 特征："预计"、"将会"、"可能"、"最终"、"预期"
  - 示例："美元将在未来10年内大幅贬值"
  - 对于预测型，填写 valid_until_note 说明时间范围

> 注意：两类结论统一放在 conclusions 数组中，用 conclusion_type 区分。

### 解决方案（Solution）
作者从结论推导出的具体行动建议。
- 必须是可执行的金融/投资/资产配置建议（买什么、卖什么、持有什么）
- 必须能追溯到作者明确陈述的结论（通过 source_conclusion_indices 引用）
- **不提取**：泛泛而谈的"应该改革"、"政府应当"等非投资性建议
- action_type 从以下选择：buy / sell / hold / short / diversify / hedge / reduce

### 逻辑（Logic）
建立推理链，分两种类型：
- **inference**（事实→结论）：
  - 每个结论都必须关联一条 inference Logic
  - supporting_fact_indices：已知支撑事实（已发生或可核实的证据）
  - assumption_fact_indices：假设条件（尚未发生或待验证的前提）
- **derivation**（结论→解决方案）：
  - 每个解决方案都必须关联一条 derivation Logic
  - source_conclusion_indices：推导所基于的结论索引

## 概念归一化（canonical_claim）

每条事实/结论都必须填写 `canonical_claim`——概念的标准化表达，用于识别不同来源的相同概念。

**归一化规则：**
- 使用标准金融/经济术语（"美联储"不用"Fed"或"美国央行"；"实际利率"不用"真实利率"）
- 统一量化表达（如 "七成" 统一为 "7/10"，"上世纪" 指明具体年代）
- 去掉修辞性限定词，保留核心概念和数字
- 中英混合内容统一转为中文

**示例：**
| 原文 claim | canonical_claim |
|---|---|
| "七成大国财富清零" | "1900-1945年间7/10强国经历财富清零" |
| "7 of 10 great powers saw wealth wiped out" | "1900-1945年间7/10强国经历财富清零" |
| "the Fed printed money" | "美联储实施量化宽松" |
| "美股长期牛市" | "美国股市长期实际收益为正" |

## 重要规则

1. **事实优先**：先完整提取所有事实，再建立逻辑关系
2. **事实独立性**：同一个事实可以同时支撑多个结论
3. **下标一致性**：logics 中的 indices 必须对应对应数组的有效下标
4. **不推断**：只提取文本中明确或强烈暗示的内容，不添加作者未说明的内容
5. **相关性**：仅当内容是纯粹的娱乐/广告/无主张分享时，设 is_relevant_content=false
6. **解决方案可选**：若文章不含具体投资建议，solutions 填空数组 []

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
- 包含多少条独立事实？有哪些结论（回顾型 vs 预测型）？有哪些具体投资建议？

**Step B：提取事实**
- 列出文章中所有可独立核实的陈述
- 将每条事实从"模糊表达"转化为"量化可核实表达"
- 为每条可验证事实填写 verification_method 和 suggested_references

**Step C：提取结论**
- 识别作者的每个判断：是回顾型（对已发生/当前事件的判断）还是预测型（对未来的判断）？
- 统一放在 conclusions 数组，用 conclusion_type 区分
- 预测型结论填写 valid_until_note

**Step D：提取解决方案**
- 识别作者提出的具体行动建议（买入/卖出/持有等资产配置建议）
- 每条建议记录 action_type、action_target、action_rationale
- 用 source_conclusion_indices 关联推导所基于的结论
- 若无具体投资建议，solutions 填 []

**Step E：建立逻辑关系**
- 为每个结论创建一条 inference Logic：区分支撑事实 vs 假设条件
- 为每个解决方案创建一条 derivation Logic：关联推导所基于的结论

{_OUTPUT_SCHEMA}
"""
