"""Layer 2 商业模式建模：从 Layer 1 提取结果 + 三表数据 → 商业模式"""

import asyncio
import json
import sqlite3
from pathlib import Path

from anchor.llm_client import chat_completion

SYSTEM = """\
你是一位资深基本面分析师。你的任务是基于 Layer 1 的提取结果和三表数据，建立公司的商业模式模型。

## 输入
你会收到两部分数据：
1. Layer 1 提取结果：表现/归因/指引/风险节点 + 叙事
2. 五年三表数据

## 输出格式
输出一个 JSON 对象：
{
  "summary": "一句话商业模式描述（≤80字，必须说清楚卖什么给谁靠什么赚钱）",
  "customers": {
    "segments": [
      {
        "name": "客户群名称",
        "revenue_source": "对应的收入来源/业务分部",
        "revenue_share": "占总收入比例（如能推算）",
        "growth_trend": "增速趋势",
        "description": "这个客户群为什么买这个产品"
      }
    ]
  },
  "products": {
    "segments": [
      {
        "name": "产品/业务名称",
        "description": "产品是什么、解决什么问题",
        "revenue_fy_latest": "最新财年收入",
        "growth_yoy": "同比增速"
      }
    ]
  },
  "cost_structure": {
    "upstream": "上游依赖是谁、成本结构如何",
    "cogs_ratio": "COGS/Revenue 比例及趋势",
    "rd_ratio": "R&D/Revenue 比例及趋势",
    "sga_ratio": "SGA/Revenue 比例及趋势",
    "asset_model": "轻资产还是重资产，依据是什么"
  },
  "revenue_structure": {
    "concentration": "收入集中度（哪个分部占比最大）",
    "diversification": "收入多元化程度",
    "trend": "结构变化趋势（哪块在变大、哪块在缩小）"
  },
  "profit_drivers": {
    "primary": "主要利润驱动力",
    "operating_leverage": "经营杠杆情况（收入增长时利润增速是否更快）",
    "margin_trend": "毛利率/营业利润率趋势"
  }
}

## 规则
1. 所有判断必须基于提供的数据，不要使用外部知识补充事实
2. 比例和趋势要用数据佐证
3. 只输出 JSON
"""


def get_financials_text() -> str:
    """从 DB 读取三表数据，格式化为文本"""
    conn = sqlite3.connect("./test_anchor.db")
    c = conn.cursor()
    lines = []

    for stype, label in [
        ("income", "利润表"),
        ("balance_sheet", "资产负债表"),
        ("cashflow", "现金流量表"),
    ]:
        lines.append(f"\n=== {label} ===")
        periods = ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]

        c.execute(
            """
            SELECT DISTINCT li.item_key, li.item_label, li.ordinal
            FROM financial_line_items li
            JOIN financial_statements s ON li.statement_id = s.id
            WHERE s.statement_type = ? AND s.company_id = 1
            ORDER BY li.ordinal
        """,
            (stype,),
        )
        items = c.fetchall()

        header = f"{'Item':>35s}"
        for p in periods:
            header += f"{p:>12s}"
        lines.append(header)

        for key, label_text, _ in items:
            row = f"{label_text:>35s}"
            for p in periods:
                c.execute(
                    """
                    SELECT li.value FROM financial_line_items li
                    JOIN financial_statements s ON li.statement_id = s.id
                    WHERE s.period = ? AND s.statement_type = ? AND li.item_key = ? AND s.company_id = 1
                """,
                    (p, stype, key),
                )
                r = c.fetchone()
                row += f"{r[0]:>12,.0f}" if r else f"{'-':>12s}"
            lines.append(row)

    conn.close()
    return "\n".join(lines)


async def main():
    # 读取 Layer 1 提取结果
    layer1 = json.loads(Path("/tmp/nvidia_layer1_extract.json").read_text())

    # 格式化 Layer 1 节点
    nodes_text = "## Layer 1 提取节点\n"
    for n in layer1["nodes"]:
        nodes_text += f"[{n['type']}] {n['claim']}\n"

    narratives_text = "\n## Layer 1 叙事\n"
    for n in layer1["narratives"]:
        narratives_text += f"叙事: {n['narrative']}\n"
        if n.get("promised_outcome"):
            narratives_text += f"承诺结果: {n['promised_outcome']}\n"

    # 读取三表数据
    fin_text = get_financials_text()

    user = f"""{nodes_text}
{narratives_text}

## 五年三表数据（单位：百万美元）
{fin_text}"""

    print("调用 LLM 建模中...")
    resp = await chat_completion(system=SYSTEM, user=user, max_tokens=4096)
    if not resp:
        print("LLM failed")
        return

    text = resp.content.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]

    model = json.loads(text)

    # 打印结果
    print("\n" + "=" * 70)
    print("NVIDIA 商业模式 (Layer 2)")
    print("=" * 70)

    print(f"\n【一句话】{model['summary']}")

    print("\n【客户】")
    for seg in model["customers"]["segments"]:
        share = seg.get("revenue_share", "?")
        print(f"  {seg['name']} ({share}): {seg['description']}")
        print(f"    收入来源: {seg['revenue_source']}, 增速: {seg['growth_trend']}")

    print("\n【产品】")
    for seg in model["products"]["segments"]:
        print(f"  {seg['name']}: {seg['description']}")
        print(f"    FY最新: {seg['revenue_fy_latest']}, YoY: {seg['growth_yoy']}")

    print("\n【成本结构】")
    cs = model["cost_structure"]
    print(f"  上游: {cs['upstream']}")
    print(f"  COGS/Rev: {cs['cogs_ratio']}")
    print(f"  R&D/Rev: {cs['rd_ratio']}")
    print(f"  SGA/Rev: {cs['sga_ratio']}")
    print(f"  资产模式: {cs['asset_model']}")

    print("\n【收入结构】")
    rs = model["revenue_structure"]
    print(f"  集中度: {rs['concentration']}")
    print(f"  多元化: {rs['diversification']}")
    print(f"  趋势: {rs['trend']}")

    print("\n【利润驱动】")
    pd = model["profit_drivers"]
    print(f"  主驱动: {pd['primary']}")
    print(f"  经营杠杆: {pd['operating_leverage']}")
    print(f"  利润率趋势: {pd['margin_trend']}")

    # 保存
    out = Path("/tmp/nvidia_layer2_business_model.json")
    out.write_text(json.dumps(model, ensure_ascii=False, indent=2))
    print(f"\n完整 JSON 已保存到 {out}")


if __name__ == "__main__":
    asyncio.run(main())
