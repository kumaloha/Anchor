"""Layer 1 提取：NVIDIA 财报 → 表现/归因/指引/风险 + 叙事"""

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

SYSTEM = """\
你是一位基本面分析师。从财报原文中按四类节点提取信息，再单独提取叙事。

## 输出格式
输出一个 JSON 对象：
{
  "nodes": [
    {
      "type": "表现|归因|指引|风险",
      "claim": "≤200字，忠于原文",
      "summary": "≤30字摘要"
    }
  ],
  "narratives": [
    {
      "narrative": "管理层讲的故事/战略承诺 ≤300字",
      "capital_required": null,
      "capital_unit": null,
      "promised_outcome": "承诺的结果 ≤200字",
      "deadline": null
    }
  ]
}

## 节点类型说明
- 表现：已发生的业绩事实（收入、利润、增速、市场份额等数字）
- 归因：为什么会这样（驱动因素、因果解释）
- 指引：管理层对未来的预期/指导（下季度收入指引、毛利率预期等）
- 风险：潜在威胁、不确定性、负面因素

## 叙事说明
叙事是管理层讲的「大故事」— 战略方向、重大投资、产品路线图。
不是单个数字，而是需要资金投入且承诺了某种结果的长期计划。

## 规则
1. 每个独立的事实/判断单独成一个节点，不要合并
2. 数字必须保留原始值和单位
3. 只输出 JSON
"""


async def main():
    content = Path("docs/nvidia_fy2026_q4_earnings.md").read_text()
    user = f"请提取以下NVIDIA财报的节点和叙事：\n\n{content}"

    resp = await chat_completion(system=SYSTEM, user=user, max_tokens=8192)
    if not resp:
        print("LLM failed")
        return

    # 清理 markdown code block
    text = resp.content.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]

    data = json.loads(text)

    print("=== 节点 (%d) ===" % len(data["nodes"]))
    for n in data["nodes"]:
        print(f"  [{n['type']}] {n['summary']}")
        claim = n["claim"]
        if len(claim) > 120:
            claim = claim[:120] + "..."
        print(f"    {claim}")
        print()

    print("=== 叙事 (%d) ===" % len(data["narratives"]))
    for n in data["narratives"]:
        narr = n["narrative"]
        if len(narr) > 120:
            narr = narr[:120] + "..."
        print(f"  {narr}")
        if n.get("promised_outcome"):
            print(f"    → {n['promised_outcome'][:100]}")
        print()

    # 保存完整 JSON 供 Layer 2 使用
    out = Path("/tmp/nvidia_layer1_extract.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"完整 JSON 已保存到 {out}")


if __name__ == "__main__":
    asyncio.run(main())
