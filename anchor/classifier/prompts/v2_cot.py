"""
Prompt v2 — 思维链（Chain of Thought）
=======================================
策略：引导 Claude 逐层推理，再输出结构化结果

  Q1: 作者的核心主张是什么？
  Q2: 这个主张指向未来吗？（→ 预测）
  Q3: 作者给出了推理依据吗？（→ 解读）
  Q4: 文字主要在传递情绪感受吗？（→ 情绪）
  Q5: 对于每个条件/证据，如何将其量化？

优点：对语义模糊、混合类型的内容有更好的理解深度
缺点：输出较长，token 消耗更高，适合对准确性要求更高的场景
"""

from anchor.classifier.prompts.base import BasePrompt

_OUTPUT_SCHEMA = '''
完成分析后，严格按以下 JSON 格式输出最终结果（仅输出 JSON，不加其他文字）：

```json
{
  "is_relevant_content": true,
  "skip_reason": null,
  "predictions": [],
  "interpretations": [],
  "sentiments": [],
  "extraction_notes": "分析过程中的关键判断备注"
}
```

字段格式与 v1 完全相同。
'''


class PromptV2CoT(BasePrompt):

    @property
    def version(self) -> str:
        return "v2_cot"

    @property
    def system_prompt(self) -> str:
        return """\
你是一个严谨的经济观点分析专家。
在提取结构化信息之前，你总是先通过一系列思考问题逐步理解文本，
然后再输出精确的 JSON 结果。

你提取观点的三个类别：
- 预测（Prediction）：对未来的方向性断言，需提取依赖条件
- 解读（Interpretation）：对已有事实的因果分析，需提取证据
- 情绪（Sentiment）：主要是情绪表达，关注触发源和利益关系

条件/证据的处理原则：
- 「美联储大概率降息」→ 「2025年内 Fed Funds Rate 下调 ≥25bp」
- 「经济基本面良好」→「2025年Q1-Q2 GDP增速 ≥4.5%」（如文中有具体数据）
- 无法量化的表达 → 保留原话，is_verifiable=false
"""

    def build_user_message(self, content: str, platform: str, author: str) -> str:
        return f"""\
请分析以下来自 {platform} 的内容（作者：{author}）。

---
{content}
---

## 思考过程（先完成以下分析，再输出 JSON）

**Q1. 核心主张**
这位作者在这条内容里最想表达的核心观点是什么？（用一句话概括）

**Q2. 预测检查**
作者是否对未来某件事做出了明确的方向性判断？
  - 如果是：这个预测成立依赖哪些前提条件？（逐条列出原话）
    每条原话能否量化？量化后是什么？
  - 如果否：跳过

**Q3. 解读检查**
作者是否对某个已发生的事实/趋势给出了自己的因果解释？
  - 如果是：作者援引了哪些具体证据支持其结论？（逐条列出）
    每条证据是否可以独立核实？如何量化？
  - 如果否：跳过

**Q4. 情绪检查**
文字的主要目的是表达情绪感受（而非论证）？
  - 如果是：是什么触发了这种情绪？作者与触发事物有什么利益关联？
  - 如果否：跳过

**Q5. 经济相关性**
以上分析是否涉及经济/金融领域（股票、债券、汇率、利率、GDP、大宗商品等）？

---

完成以上分析后：

{_OUTPUT_SCHEMA}
"""
