"""
Prompt v3_unified — 四实体统一提取（事实/结论/条件/解决方案/逻辑）
================================================================
策略（五步）：
  Step A：判断相关性
  Step B：提取事实（Fact）— 含 verifiable_statement
  Step C：提取结论（Conclusion）— 回顾型 + 预测型统一，含 verifiable_statement
  Step D：提取解决方案（Solution）
  Step E：提取条件（Condition）— 统一假设条件(assumption) + 隐含条件(implicit)
  Step F：建立逻辑关系（Logic）— 仅 inference 和 derivation 两种类型
"""

from anchor.extract.prompts.base import BasePrompt

_OUTPUT_SCHEMA = """
请严格按照以下 JSON 格式输出，不要输出任何其他内容：

```json
{
  "is_relevant_content": true,
  "skip_reason": null,
  "facts": [
    {
      "claim": "事实的核心陈述（≤80字，保留原意）",
      "canonical_claim": "概念归一化标准形式（≤60字，标准术语，跨帖匹配用）",
      "verifiable_statement": "单句可核实陈述，含量化指标和时间范围，格式：主语+谓语+量化结果+时间",
      "temporal_type": "retrospective",
      "temporal_note": "事实有效时间范围（如'2023年Q1-Q3'），无则null",
      "verifiable_expression": "含量化指标的可核实表达，或null",
      "is_verifiable": true,
      "verification_method": "具体验证操作：对比哪段时间的哪个数据，判定阈值是什么"
    }
  ],
  "conclusions": [
    {
      "topic": "话题名称（≤20字）",
      "claim": "核心结论陈述（第三人称，≤80字）",
      "canonical_claim": "概念归一化标准形式（≤60字，标准术语）",
      "verifiable_statement": "单句可核实陈述，格式：主语+谓语+量化结果+时间（如有）",
      "conclusion_type": "retrospective",
      "temporal_note": "预测型必填时间范围（如'2025-2030年'），回顾型填null",
      "author_confidence": "certain|likely|uncertain|speculative",
      "author_confidence_note": "作者原文中表达自信/不确定性的具体语句（≤40字），无则null"
    }
  ],
  "solutions": [
    {
      "topic": "话题名称（≤20字）",
      "claim": "行动建议内容（第三人称，≤100字）",
      "canonical_claim": "概念归一化标准形式（≤60字），或null",
      "action_type": "buy|sell|hold|short|diversify|hedge|reduce|avoid|advocate",
      "action_target": "标的物，如'黄金ETF'、'科技股'",
      "action_rationale": "一句话说明从哪些结论推导出此建议"
    }
  ],
  "conditions": [
    {
      "condition_text": "条件陈述（≤60字）",
      "condition_type": "assumption",
      "verifiable_statement": "可核实的条件表达（若无法核实则null）",
      "temporal_note": "条件相关时间范围，无则null",
      "is_consensus": false,
      "is_verifiable": false
    }
  ],
  "logics": [
    {
      "logic_type": "inference",
      "target_conclusion_index": 0,
      "supporting_fact_indices": [0, 1],
      "supporting_condition_indices": [0],
      "supporting_conclusion_indices": []
    },
    {
      "logic_type": "derivation",
      "solution_index": 0,
      "source_conclusion_indices": [0, 1]
    }
  ],
  "extraction_notes": null
}
```
"""


class PromptV3Unified(BasePrompt):

    @property
    def version(self) -> str:
        return "v3_unified"

    @property
    def system_prompt(self) -> str:
        return """你是一个专业的观点分析助手。你的任务是从社交媒体文本中提取有实质内容的主张，
并将其结构化为四类实体：事实（Fact）、结论（Conclusion）、条件（Condition）、解决方案（Solution），
以及连接它们的逻辑边（Logic）。

## 采集范围

以下领域的主张均在采集范围内：
- **经济/金融**：市场判断、政策分析、宏观经济判断、行业趋势、投资建议
- **政治**：政策走向、政治人物/政党行为、地缘政治、选举结果预测
- **社会/文化**：社会现象的成因解读、文化趋势预测、社会变迁

---

## 四类实体定义

### 事实（Fact）
文章中出现的、可以独立核实的陈述。
- 与结论和解决方案解耦——事实本身不依赖任何观点
- 可以是已发生事件、统计数据、官方决策、已知规律
- 每条事实必须填写 `verifiable_statement`：一句完整、可搜索验证的陈述
  - 格式：主语 + 谓语 + 量化结果（如有） + 时间范围
  - 示例："美国2022年GDP增长2.1%"、"2024年美联储降息3次共75个基点"
- `temporal_type`：若为历史事实填 "retrospective"；若为已明确时间的预设事实填 "predictive"

### 结论（Conclusion）
作者的分析判断，统一放在 conclusions 数组，用 `conclusion_type` 区分：
- **回顾型（retrospective）**：对过去或当前事件的判断
  - 特征："已经发生"、"现在处于"、"是"、"导致了"、"正在"
  - 示例："过去150年间大周期循环导致财富更替"
- **预测型（predictive）**：对未来事件或趋势的判断
  - 特征："预计"、"将会"、"可能"、"最终"、"预期"、"迟早"
  - 示例："美元将在未来10年内大幅贬值"
  - **预测型必须填写 `temporal_note`**（时间范围，如"2025-2030年"）

每个结论都必须填写 `verifiable_statement`：一句完整的可核实陈述。

**作者自信度（author_confidence）：**
- `certain`     — 明确断言："一定会"、"毫无疑问"、"必然"、"肯定"
- `likely`      — 认为很可能："预计"、"很可能"、"应该"（无显著限定词时默认此档）
- `uncertain`   — 明确不确定："也许"、"不太确定"、"有可能不准"
- `speculative` — 明确标注猜测："只是猜测"、"纯属个人看法"、"仅供参考"

### 解决方案（Solution）
作者从结论推导出的具体行动建议。
- 必须是可执行的行动建议（投资/政策/资产配置等）
- 必须能追溯到作者明确陈述的结论（通过 Logic derivation 关联）
- **不提取**：泛泛而谈的"应该改革"等无法操作的建议
- `action_type`：buy / sell / hold / short / diversify / hedge / reduce / avoid / advocate

### 条件（Condition）
与结论相关联的前提条件，分两种类型：
- **假设条件（assumption）**：作者明确陈述的"如果X则Y"前提
  - 特征："如果"、"假设"、"前提是"、"只要"、"一旦X发生"
  - 示例："如果美联储继续维持高利率"、"假设中美不发生直接冲突"
  - `is_verifiable`：此假设是否可以被现实核实（一般填 true）
- **隐含条件（implicit）**：推理链中未明说但必须成立的暗含前提
  - 特征：从文章行文逻辑中推断，作者未直接陈述
  - 示例："历史规律会重演"、"政府政策目标会保持一致"
  - `is_consensus`：是否为普遍共识（若是，验证时可跳过）

---

## 逻辑关系（Logic）

仅有两种类型：

### inference（推理）— 前提 → 结论
每个结论都应有一条 inference Logic（无明显前提时也创建，前提列表置空）：
- `target_conclusion_index`：目标结论在 conclusions[] 中的下标
- `supporting_fact_indices`：支撑事实下标（来自 facts[]）
- `supporting_condition_indices`：依赖条件下标（来自 conditions[]）
- `supporting_conclusion_indices`：作为前提的子结论下标（来自 conclusions[]，支持结论→结论链）

### derivation（推导）— 结论 → 解决方案
每个解决方案都应有一条 derivation Logic：
- `solution_index`：目标解决方案在 solutions[] 中的下标
- `source_conclusion_indices`：推导所基于的结论下标（来自 conclusions[]）

---

## 概念归一化（canonical_claim）

事实和结论必须填写 `canonical_claim`——概念的标准化表达，用于识别不同来源的相同概念。

**规则：**
- 使用标准金融/经济术语（"美联储"不用"Fed"；"实际利率"不用"真实利率"）
- 统一量化表达（"七成" → "7/10"）
- 去掉修辞性限定词，保留核心概念和数字
- 中英混合内容统一转为中文

---

## 重要规则

1. **事实优先**：先完整提取所有事实，再建立逻辑关系
2. **下标一致性**：logics 中的 indices 必须是对应数组的有效下标（0起始）
3. **不推断**：只提取文本中明确或强烈暗示的内容
4. **相关性**：仅当内容是纯粹的娱乐/广告/无主张分享时，设 is_relevant_content=false
5. **条件可为空**：若文章没有明显的条件前提，conditions 填 []
6. **解决方案可为空**：若文章不含具体行动建议，solutions 填 []

## X 长文特殊处理

当内容含 `[X长文·仅预览]` 标记时：
- 根据标题和可见摘要判断主题
- 若标题指向经济/金融/政治/社会分析，设 is_relevant_content=true
- 从标题和摘要中尽量提取；信息不足时可建立一条概括性结论
- extraction_notes 注明"X长文内容截断，提取仅基于标题和预览"
"""

    def build_user_message(self, content: str, platform: str, author: str) -> str:
        return f"""请分析以下来自 {platform} 的内容（作者：{author}）：

---
{content}
---

请按以下步骤思考：

**Step A：相关性判断**
- 这条内容是否包含有实质主张（经济/政治/社会/文化均可）？
- 若是纯粹的娱乐/广告/生活分享，设 is_relevant_content=false

**Step B：提取事实**
- 列出文章中所有可独立核实的陈述
- 为每条事实填写 verifiable_statement（主语+谓语+量化结果+时间，一句完整陈述）
- 判断 temporal_type：历史事实=retrospective，明确时间的预设事实=predictive
- 填写 canonical_claim（标准化表达，用于跨帖去重）

**Step C：提取结论**
- 识别每个判断：回顾型（已发生/当前）还是预测型（未来）？
- 为每个结论填写 verifiable_statement（一句完整可核实陈述）
- 预测型结论必须填写 temporal_note（时间范围）
- 判断 author_confidence 并摘录 author_confidence_note

**Step D：提取解决方案**
- 识别具体可执行的行动建议（买入/卖出/持有/倡导等）
- 若无具体建议，solutions 填 []

**Step E：提取条件**
- 识别假设条件（assumption）：作者明确陈述的"如果X则Y"前提
- 识别隐含条件（implicit）：推理中必须成立但未说出的暗含前提
- 若无明显条件，conditions 填 []
- 对隐含条件判断 is_consensus（是否为普遍共识）

**Step F：建立逻辑关系**
- 为每个结论创建一条 inference Logic（填写支撑事实/条件/子结论的下标）
- 为每个解决方案创建一条 derivation Logic（填写推导所基于的结论下标）
- 检查所有 indices 均为有效下标

{_OUTPUT_SCHEMA}
"""
