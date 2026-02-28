"""
Prompt v3 — 对抗验证（Adversarial Verification）
=================================================
策略：提取后，主动寻找自己提取结论的漏洞，再修正

  Round 1：正常提取（同 v1）
  Round 2：对抗——"这个条件真的可以验证吗？时间范围足够具体吗？"
            "这个结论真的是预测而不是解读吗？"
  Round 3：根据对抗结果修正最终输出

优点：对「条件的可验证性」判断最准确，能识别伪装成预测的泛泛而谈
缺点：token 消耗最高，适合人工审核前的精处理
"""

from anchor.classifier.prompts.base import BasePrompt


class PromptV3Adversarial(BasePrompt):

    @property
    def version(self) -> str:
        return "v3_adversarial"

    @property
    def system_prompt(self) -> str:
        return """\
你是一个极其严谨的经济观点审核专家，专门负责识别那些「看起来是预测，实际上无法被验证」的模糊表达。

你的工作流程分三轮：
  Round 1：初步提取观点
  Round 2：对每个提取结果发起质疑
  Round 3：根据质疑结果修正，输出最终 JSON

## 质疑问题库

对于每个提取出的观点，你会自问：

【预测类】
  - 这个预测有没有明确的截止时间？（"今年底"比"未来"具体，"2025年Q4前"最好）
  - 条件的 verifiable_expression 是否包含可测量的数字或明确的事件？
    若只写了"经济好转"而无具体指标，则 is_verifiable=false
  - 这个预测是否只是一个期望/愿望而非判断？（"希望A股涨" ≠ 预测）
  - 条件中有没有「循环论证」？（"如果A股上涨，则A股看涨" 是无效条件）

【解读类】
  - 作者的结论是否真的基于证据推导，而非仅仅是重复陈述？
  - 证据是否具体？（"数据显示"比不上"2024年GDP增长4.9%"）
  - 逻辑链是否有明显跳跃？（标注在 extraction_notes 中）

【情绪类】
  - author_relation 是否正确？（自称持仓 → direct；纯旁观评论 → observer）
  - 情绪强度是否与文字语气匹配？
"""

    def build_user_message(self, content: str, platform: str, author: str) -> str:
        return f"""\
请分析以下来自 {platform} 的内容（作者：{author}）。

---
{content}
---

## Round 1：初步提取

首先，按照标准流程提取所有可能的观点（预测/解读/情绪）。
在这一轮，你可以写得宽松一些，先把候选项都列出来。

## Round 2：对抗质疑

对 Round 1 的每个提取结果，逐一回答以下问题：

1. 这真的是一个「预测/解读/情绪」吗？还是我分类错了？
2. 关键条件/证据的 verifiable_expression 是否足够具体和可测量？
3. 有没有遗漏重要的隐含条件？
4. 有没有把作者没说的内容错误地推断进去？

## Round 3：最终输出

根据上面两轮的分析，输出修正后的最终结果。
严格按以下 JSON 格式，仅输出 JSON，不加任何其他文字：

```json
{{
  "is_relevant_content": true,
  "skip_reason": null,
  "predictions": [
    {{
      "topic": "...",
      "claim": "...",
      "summary": "...",
      "valid_until_note": null,
      "conditions": [
        {{
          "abstract_expression": "...",
          "verifiable_expression": "...",
          "is_verifiable": true
        }}
      ]
    }}
  ],
  "interpretations": [
    {{
      "topic": "...",
      "conclusion": "...",
      "summary": "...",
      "evidence_conditions": []
    }}
  ],
  "sentiments": [
    {{
      "topic": "...",
      "summary": "...",
      "trigger_event": "...",
      "trigger_event_time_note": null,
      "emotion_cause": null,
      "emotion_label": "...",
      "emotion_intensity": 0.7,
      "author_relation": "observer",
      "author_relation_note": null
    }}
  ],
  "extraction_notes": "Round 2 中发现的关键修正点"
}}
```
"""
