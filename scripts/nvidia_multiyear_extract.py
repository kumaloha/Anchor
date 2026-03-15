"""NVIDIA 多年 10-K 并行提取 — 16 张表 × 5 个财年"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

FISCAL_YEARS = ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]

SYSTEM = """\
你是一位资深基本面分析师。从公司 10-K 年报原文中提取以下全部结构化信息。

## 输出格式
输出一个 JSON 对象。如果原文中没有某类信息，对应字段返回空数组 []。

```json
{
  "operational_issues": [
    {
      "topic": "议题名 ≤30字",
      "performance": "表现（定性描述，不含财务数字）≤200字",
      "attribution": "归因 ≤200字",
      "risk": "风险 ≤200字",
      "guidance": "指引 ≤200字"
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
  ],
  "downstream_segments": [
    {
      "segment_name": "业务分部名称",
      "product_or_service": "产品/服务",
      "customer_type": "客户类型",
      "key_customers": "重要客户",
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "同比增速",
      "description": "补充说明"
    }
  ],
  "upstream_segments": [
    {
      "supplier_name": "供应商名称",
      "supply_type": "manufacturing|component|software|service|logistics",
      "material_or_service": "供应内容",
      "concentration_risk": "集中度风险",
      "cost_share": null,
      "description": "补充说明"
    }
  ],
  "geographic_revenues": [
    {
      "region": "地域名称",
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "增速"
    }
  ],
  "non_financial_kpis": [
    {
      "kpi_name": "指标名称",
      "kpi_value": "值",
      "kpi_unit": "单位",
      "yoy_change": "变化",
      "category": "workforce|customer|product|esg|operational"
    }
  ],
  "debt_obligations": [
    {
      "instrument_name": "债务工具名称",
      "debt_type": "bond|loan|lease|convertible|credit_facility",
      "principal": null,
      "interest_rate": null,
      "maturity_date": null
    }
  ],
  "litigations": [
    {
      "case_name": "案件名称",
      "case_type": "lawsuit|regulatory|patent|antitrust|environmental|tax|other",
      "status": "pending|settled|dismissed|ongoing|appealed",
      "counterparty": null,
      "description": "案情摘要"
    }
  ],
  "executive_compensations": [
    {
      "name": "姓名",
      "title": "职位",
      "base_salary": null,
      "bonus": null,
      "stock_awards": null,
      "total_comp": null
    }
  ],
  "equity_items": [
    {
      "item_key": "标准化键",
      "item_label": "原始标签",
      "value": 0
    }
  ],
  "tax_items": [
    {
      "item_key": "标准化键",
      "item_label": "原始标签",
      "value": 0
    }
  ],
  "sbc_items": [
    {
      "item_key": "标准化键",
      "item_label": "原始标签",
      "value": 0
    }
  ],
  "related_party_transactions": [
    {
      "related_party": "关联方名称",
      "relationship": "director|officer|major_shareholder|subsidiary|affiliate|family",
      "transaction_type": "sale|purchase|lease|loan|guarantee|service|license|other",
      "amount": null,
      "description": "交易说明"
    }
  ]
}
```

## 经营议题表说明（最重要）
operational_issues 提取自 CEO致股东信、MD&A 等定性讨论段落。
- 每行 = 一个经营议题（如"数据中心需求"、"供应链管理"、"中国市场出口管制"）
- performance: 管理层对该议题的定性描述（不要放财务数字，财务数字在三表里）
- attribution: 为什么出现这个表现
- risk: 该议题面临什么风险
- guidance: 管理层对未来的展望/指引
- 四个字段都是 Optional，没提到就留 null

## 规则
1. 每个独立事实单独成条目
2. 数字保留原始值和单位，金额统一百万美元
3. revenue_share/cost_share 为 0-1 比例
4. 无数据则返回空数组
5. 只输出 JSON
"""


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def extract_one(fy: str) -> tuple[str, dict | None]:
    """提取单个财年"""
    path = Path(f"/tmp/nvidia_10k_{fy}.txt")
    content = path.read_text()
    user = f"请从以下 NVIDIA {fy.upper()} 10-K 年报中提取全部结构化信息：\n\n{content}"

    print(f"[{fy}] 调用 LLM... ({len(content):,} chars)")
    try:
        resp = await chat_completion(system=SYSTEM, user=user, max_tokens=16384)
        if not resp:
            print(f"[{fy}] FAILED - no response")
            return fy, None
        print(f"[{fy}] 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        data = clean_json(resp.content)
        # save individual result
        Path(f"/tmp/nvidia_10k_{fy}_extract.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        return fy, data
    except Exception as e:
        print(f"[{fy}] ERROR: {e}")
        return fy, None


async def main():
    # 并行提取 5 个财年（限制并发为 3 避免 rate limit）
    sem = asyncio.Semaphore(3)

    async def limited(fy):
        async with sem:
            return await extract_one(fy)

    results = await asyncio.gather(*[limited(fy) for fy in FISCAL_YEARS])
    all_data = {fy: data for fy, data in results if data}

    # 保存合并结果
    Path("/tmp/nvidia_multiyear_extract.json").write_text(
        json.dumps(all_data, ensure_ascii=False, indent=2)
    )

    # ── 打印对比报告 ──
    TABLES = [
        ("经营议题", "operational_issues"),
        ("叙事", "narratives"),
        ("下游商业模式", "downstream_segments"),
        ("上游商业模式", "upstream_segments"),
        ("地域收入", "geographic_revenues"),
        ("非财务KPI", "non_financial_kpis"),
        ("债务明细", "debt_obligations"),
        ("诉讼/或有", "litigations"),
        ("管理层薪酬", "executive_compensations"),
        ("股东权益变动", "equity_items"),
        ("所得税明细", "tax_items"),
        ("SBC明细", "sbc_items"),
        ("关联交易", "related_party_transactions"),
    ]

    lines = []
    lines.append("=" * 100)
    lines.append("NVIDIA 多年 10-K 提取对比报告")
    lines.append("=" * 100)

    # ── 1. 覆盖率总表 ──
    lines.append("")
    lines.append("─" * 100)
    lines.append("【覆盖率总表】各表条目数")
    lines.append("─" * 100)
    header = f"{'表名':<20}" + "".join(f"{fy:>10}" for fy in FISCAL_YEARS)
    lines.append(header)
    lines.append("-" * (20 + 10 * len(FISCAL_YEARS)))
    for label, key in TABLES:
        row = f"{label:<20}"
        for fy in FISCAL_YEARS:
            d = all_data.get(fy, {})
            n = len(d.get(key, []))
            row += f"{n:>10}"
        lines.append(row)

    # ── 2. 各表详细内容（逐年展示） ──
    for label, key in TABLES:
        lines.append("")
        lines.append("=" * 100)
        lines.append(f"【{label}】")
        lines.append("=" * 100)

        for fy in FISCAL_YEARS:
            d = all_data.get(fy, {})
            items = d.get(key, [])
            lines.append(f"\n  ── {fy.upper()} ({len(items)} 条) ──")

            if not items:
                lines.append("    （无数据）")
                continue

            for i, item in enumerate(items, 1):
                if key == "operational_issues":
                    lines.append(f"    {i}. 【{item.get('topic', '?')}】")
                    if item.get("performance"):
                        lines.append(f"       表现: {item['performance']}")
                    if item.get("attribution"):
                        lines.append(f"       归因: {item['attribution']}")
                    if item.get("risk"):
                        lines.append(f"       风险: {item['risk']}")
                    if item.get("guidance"):
                        lines.append(f"       指引: {item['guidance']}")
                elif key == "narratives":
                    lines.append(f"    {i}. {item.get('narrative', '?')[:150]}")
                    if item.get("promised_outcome"):
                        lines.append(f"       → {item['promised_outcome'][:100]}")
                elif key == "downstream_segments":
                    rev = f"${item['revenue']:,.0f}M" if item.get("revenue") else "?"
                    share = f"{item['revenue_share']*100:.1f}%" if item.get("revenue_share") else "?"
                    lines.append(
                        f"    {i}. {item.get('segment_name', '?')}: "
                        f"rev={rev}, share={share}, YoY={item.get('growth_yoy', '?')}"
                    )
                elif key == "upstream_segments":
                    lines.append(
                        f"    {i}. {item.get('supplier_name', '?')} "
                        f"[{item.get('supply_type', '?')}]: "
                        f"{item.get('material_or_service', '?')}"
                    )
                    if item.get("concentration_risk"):
                        lines.append(f"       集中度: {item['concentration_risk']}")
                elif key == "geographic_revenues":
                    rev = f"${item['revenue']:,.0f}M" if item.get("revenue") else "?"
                    lines.append(
                        f"    {i}. {item.get('region', '?')}: "
                        f"rev={rev}, YoY={item.get('growth_yoy', '?')}"
                    )
                elif key == "non_financial_kpis":
                    lines.append(
                        f"    {i}. [{item.get('category', '?')}] "
                        f"{item.get('kpi_name', '?')}: "
                        f"{item.get('kpi_value', '?')} {item.get('kpi_unit', '')}"
                    )
                elif key == "debt_obligations":
                    lines.append(
                        f"    {i}. {item.get('instrument_name', '?')}: "
                        f"${item.get('principal', '?')}M, "
                        f"rate={item.get('interest_rate', '?')}, "
                        f"maturity={item.get('maturity_date', '?')}"
                    )
                elif key == "litigations":
                    lines.append(
                        f"    {i}. {item.get('case_name', '?')} "
                        f"[{item.get('status', '?')}]: "
                        f"{item.get('description', '')[:120]}"
                    )
                elif key == "executive_compensations":
                    lines.append(
                        f"    {i}. {item.get('name', '?')} ({item.get('title', '?')}): "
                        f"total=${item.get('total_comp', '?')}"
                    )
                elif key == "related_party_transactions":
                    lines.append(
                        f"    {i}. {item.get('related_party', '?')} "
                        f"({item.get('relationship', '?')}): "
                        f"{item.get('transaction_type', '?')}, "
                        f"amt={item.get('amount', '?')}"
                    )
                else:
                    lines.append(
                        f"    {i}. {item.get('item_key', '?')}: "
                        f"{item.get('value', '?')} ({item.get('item_label', '')})"
                    )

    output = "\n".join(lines)
    out_path = Path("/tmp/nvidia_multiyear_report.txt")
    out_path.write_text(output)
    print("\n" + output)
    print(f"\n已保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
