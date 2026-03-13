"""NVIDIA FY2025 Annual Review — 全量提取到 16 张表"""

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

content = Path("/tmp/nvidia_annual_review_2025_text.md").read_text()

SYSTEM = """\
你是一位资深基本面分析师。从公司年报原文中提取以下全部结构化信息。

## 输出格式
输出一个 JSON 对象。如果原文中没有某类信息，对应字段返回空数组 []。

```json
{
  "operations": [{"type": "表现|归因|指引|风险", "claim": "≤200字", "summary": "≤30字"}],
  "narratives": [{"narrative": "≤300字", "capital_required": null, "capital_unit": null, "promised_outcome": "≤200字", "deadline": null}],
  "downstream_segments": [{"segment_name": "", "product_or_service": "", "customer_type": "", "key_customers": "", "revenue": null, "revenue_share": null, "growth_yoy": "", "description": ""}],
  "upstream_segments": [{"supplier_name": "", "supply_type": "manufacturing|component|software|service|logistics", "material_or_service": "", "concentration_risk": "", "cost_share": null, "description": ""}],
  "geographic_revenues": [{"region": "", "revenue": null, "revenue_share": null, "growth_yoy": ""}],
  "non_financial_kpis": [{"kpi_name": "", "kpi_value": "", "kpi_unit": "", "yoy_change": "", "category": "workforce|customer|product|esg|operational"}],
  "debt_obligations": [{"instrument_name": "", "debt_type": "", "principal": null, "interest_rate": null, "maturity_date": null}],
  "litigations": [{"case_name": "", "case_type": "", "status": "", "counterparty": null, "description": ""}],
  "executive_compensations": [{"name": "", "title": "", "total_comp": null}],
  "equity_items": [{"item_key": "", "item_label": "", "value": 0}],
  "tax_items": [{"item_key": "", "item_label": "", "value": 0}],
  "sbc_items": [{"item_key": "", "item_label": "", "value": 0}],
  "related_party_transactions": [{"related_party": "", "relationship": "", "transaction_type": "", "amount": null, "description": ""}]
}
```

## 规则
1. 每个独立事实单独成条目
2. 数字保留原始值和单位，金额统一百万美元
3. revenue_share/cost_share 为0-1比例
4. 无数据则返回空数组
5. 只输出JSON
"""


def clean_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def main():
    user = f"请从以下 NVIDIA FY2025 年报中提取全部结构化信息：\n\n{content}"

    print("调用 LLM...")
    resp = await chat_completion(system=SYSTEM, user=user, max_tokens=16384)
    if not resp:
        print("FAILED")
        return

    data = clean_json(resp.content)
    Path("/tmp/nvidia_fy2025_annual_extract.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )

    lines = []
    lines.append("=" * 80)
    lines.append("NVIDIA FY2025 Annual Review — Layer 1 全量提取结果")
    lines.append("=" * 80)
    lines.append("")

    # 1. Operations
    ops = data.get("operations", [])
    lines.append("─" * 80)
    lines.append(f"【1. 经营表】共 {len(ops)} 条")
    lines.append("─" * 80)
    for i, o in enumerate(ops, 1):
        lines.append(f"  {i}. [{o['type']}] {o['summary']}")
        lines.append(f"     {o['claim']}")
        lines.append("")

    # 2. Narratives
    narrs = data.get("narratives", [])
    lines.append("─" * 80)
    lines.append(f"【2. 叙事表】共 {len(narrs)} 条")
    lines.append("─" * 80)
    for i, n in enumerate(narrs, 1):
        lines.append(f"  {i}. {n['narrative'][:150]}...")
        if n.get("promised_outcome"):
            lines.append(f"     → {n['promised_outcome'][:150]}")
        if n.get("capital_required"):
            lines.append(f"     资金: {n['capital_required']} {n.get('capital_unit', '')}")
        lines.append("")

    # 3. Downstream
    ds = data.get("downstream_segments", [])
    lines.append("─" * 80)
    lines.append(f"【3. 下游商业模式表】共 {len(ds)} 条")
    lines.append("─" * 80)
    for i, s in enumerate(ds, 1):
        rev = f"${s['revenue']:,.0f}M" if s.get("revenue") else "?"
        share = f"{s['revenue_share']*100:.1f}%" if s.get("revenue_share") else "?"
        lines.append(f"  {i}. {s['segment_name']}")
        lines.append(f"     产品: {s.get('product_or_service', '-')}")
        lines.append(f"     客户: {s.get('customer_type', '-')}")
        lines.append(f"     重要客户: {s.get('key_customers', '-')}")
        lines.append(f"     收入: {rev}  占比: {share}  YoY: {s.get('growth_yoy', '-')}")
        lines.append("")

    # 4. Upstream
    us = data.get("upstream_segments", [])
    lines.append("─" * 80)
    lines.append(f"【4. 上游商业模式表】共 {len(us)} 条")
    lines.append("─" * 80)
    if not us:
        lines.append("  （无数据）")
    for i, s in enumerate(us, 1):
        lines.append(f"  {i}. {s.get('supplier_name', '?')} [{s.get('supply_type', '?')}]")
        lines.append(f"     供应: {s.get('material_or_service', '-')}")
        if s.get("concentration_risk"):
            lines.append(f"     集中度风险: {s['concentration_risk']}")
        if s.get("description"):
            lines.append(f"     说明: {s['description']}")
        lines.append("")

    # 5. Geographic
    geo = data.get("geographic_revenues", [])
    lines.append("─" * 80)
    lines.append(f"【5. 地域收入表】共 {len(geo)} 条")
    lines.append("─" * 80)
    if not geo:
        lines.append("  （无数据）")
    for i, g in enumerate(geo, 1):
        rev = f"${g['revenue']:,.0f}M" if g.get("revenue") else "?"
        lines.append(f"  {i}. {g['region']}: {rev}  YoY: {g.get('growth_yoy', '-')}")
    lines.append("")

    # 6. KPIs
    kpis = data.get("non_financial_kpis", [])
    lines.append("─" * 80)
    lines.append(f"【6. 非财务KPI表】共 {len(kpis)} 条")
    lines.append("─" * 80)
    if not kpis:
        lines.append("  （无数据）")
    for i, k in enumerate(kpis, 1):
        lines.append(
            f"  {i}. [{k.get('category', '?')}] {k['kpi_name']}: "
            f"{k['kpi_value']} {k.get('kpi_unit', '')}  "
            f"变化: {k.get('yoy_change', '-')}"
        )
    lines.append("")

    # 7-13: remaining
    table_map = [
        ("7. 债务明细", "debt_obligations"),
        ("8. 诉讼/或有", "litigations"),
        ("9. 管理层薪酬", "executive_compensations"),
        ("10. 股东权益变动", "equity_items"),
        ("11. 所得税明细", "tax_items"),
        ("12. SBC明细", "sbc_items"),
        ("13. 关联交易", "related_party_transactions"),
    ]
    for label, key in table_map:
        items = data.get(key, [])
        lines.append("─" * 80)
        lines.append(f"【{label}】共 {len(items)} 条")
        lines.append("─" * 80)
        if not items:
            lines.append("  （无数据）")
        else:
            for i, item in enumerate(items, 1):
                lines.append(f"  {i}. {json.dumps(item, ensure_ascii=False)}")
        lines.append("")

    # Coverage
    lines.append("=" * 80)
    lines.append("覆盖率统计")
    lines.append("=" * 80)
    all_keys = [
        "operations", "narratives", "downstream_segments", "upstream_segments",
        "geographic_revenues", "non_financial_kpis", "debt_obligations", "litigations",
        "executive_compensations", "equity_items", "tax_items", "sbc_items",
        "related_party_transactions",
    ]
    covered = sum(1 for k in all_keys if data.get(k))
    lines.append(f"  {covered}/{len(all_keys)} 个表有数据")
    empty = [k for k in all_keys if not data.get(k)]
    if empty:
        empty_str = ", ".join(empty)
        lines.append(f"  空表: {empty_str}")

    output = "\n".join(lines)
    out_path = Path("/tmp/nvidia_fy2025_annual_full_report.txt")
    out_path.write_text(output)
    print(output)
    print(f"\n已保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
