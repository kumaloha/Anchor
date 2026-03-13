"""
NVIDIA 财报全量提取测试 — 覆盖 Layer 1 全部表结构
================================================================
从 earnings press release 提取并写入：
  1. 经营表（表现/归因/指引/风险）→ ExtractionNode
  2. 叙事表 → CompanyNarrative
  3. 三表 → FinancialStatement + FinancialLineItem（已有，跳过）
  4. 下游商业模式表 → DownstreamSegment
  5. 上游商业模式表 → UpstreamSegment
  6. 地域收入表 → GeographicRevenue
  7. 非财务KPI表 → NonFinancialKPI
  8. 债务明细 → DebtObligation
  9. 诉讼/或有事项 → Litigation
  10. 管理层薪酬 → ExecutiveCompensation
  11. 股东权益变动表 → FinancialStatement(equity)
  12. 所得税明细 → FinancialStatement(tax_detail)
  13. SBC明细 → FinancialStatement(sbc_detail)

注：press release 不含全部 10-K 内容（无诉讼、薪酬、税务详情），
    本脚本验证提取管线能力，缺失内容 LLM 应返回空数组。
"""

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

# ── 提取文档 ──
EARNINGS_PATH = Path("docs/nvidia_fy2026_q4_earnings.md")

# ── Prompt：一次性提取全部结构化信息 ──
SYSTEM = """\
你是一位资深基本面分析师。从公司财报/年报原文中提取以下全部结构化信息。

## 输出格式
输出一个 JSON 对象，包含以下全部字段。如果原文中没有某类信息，对应字段返回空数组 []。

```json
{
  "operations": [
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
      "maturity_date": null,
      "is_secured": false,
      "is_current": false,
      "note": null
    }
  ],
  "litigations": [
    {
      "case_name": "案件名称",
      "case_type": "lawsuit|regulatory|patent|antitrust|environmental|tax|other",
      "status": "pending|settled|dismissed|ongoing|appealed",
      "counterparty": null,
      "claimed_amount": null,
      "accrued_amount": null,
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
      "option_awards": null,
      "non_equity_incentive": null,
      "other_comp": null,
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
      "terms": "交易条件/定价依据",
      "is_ongoing": false,
      "description": "交易说明"
    }
  ]
}
```

## 规则
1. 每个独立的事实/判断单独成一个条目，不要合并
2. 数字必须保留原始值和单位
3. revenue/principal 等金额单位统一为百万美元
4. revenue_share/cost_share 是 0-1 之间的比例
5. 如果原文没有某类信息，返回空数组 []
6. 只输出 JSON，不要输出其他内容
"""


def clean_json(text: str) -> dict:
    """清理 LLM 输出中的 markdown code block"""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def main():
    content = EARNINGS_PATH.read_text()
    user = f"请从以下 NVIDIA FY2026 Q4 财报中提取全部结构化信息：\n\n{content}"

    print("调用 LLM 提取中...")
    resp = await chat_completion(system=SYSTEM, user=user, max_tokens=8192)
    if not resp:
        print("LLM 调用失败")
        return

    data = clean_json(resp.content)

    # 保存完整结果
    out = Path("/tmp/nvidia_full_extract.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # 打印各表提取结果统计
    print("\n" + "=" * 60)
    print("NVIDIA FY2026 Q4 — 全量提取结果")
    print("=" * 60)

    sections = [
        ("经营表 (operations)", "operations"),
        ("叙事表 (narratives)", "narratives"),
        ("下游商业模式 (downstream)", "downstream_segments"),
        ("上游商业模式 (upstream)", "upstream_segments"),
        ("地域收入 (geographic)", "geographic_revenues"),
        ("非财务KPI (kpis)", "non_financial_kpis"),
        ("债务明细 (debt)", "debt_obligations"),
        ("诉讼/或有 (litigation)", "litigations"),
        ("管理层薪酬 (exec comp)", "executive_compensations"),
        ("股东权益变动 (equity)", "equity_items"),
        ("所得税明细 (tax)", "tax_items"),
        ("SBC明细 (sbc)", "sbc_items"),
        ("关联交易 (related party)", "related_party_transactions"),
    ]

    for label, key in sections:
        items = data.get(key, [])
        status = f"{len(items)} 条" if items else "⚠️  无数据（原文未包含）"
        print(f"\n  {label}: {status}")
        if items:
            for i, item in enumerate(items[:5], 1):  # 最多显示5条
                if key == "operations":
                    print(f"    {i}. [{item['type']}] {item['summary']}")
                elif key == "narratives":
                    narr = item['narrative'][:80]
                    print(f"    {i}. {narr}...")
                elif key == "downstream_segments":
                    print(f"    {i}. {item['segment_name']}: rev={item.get('revenue')}, share={item.get('revenue_share')}")
                elif key == "upstream_segments":
                    print(f"    {i}. {item.get('supplier_name', '?')}: {item.get('material_or_service', '?')}")
                elif key == "geographic_revenues":
                    print(f"    {i}. {item['region']}: rev={item.get('revenue')}, share={item.get('revenue_share')}")
                elif key == "non_financial_kpis":
                    print(f"    {i}. {item['kpi_name']}: {item['kpi_value']} {item.get('kpi_unit', '')}")
                elif key == "debt_obligations":
                    print(f"    {i}. {item['instrument_name']}: {item.get('principal')}M, rate={item.get('interest_rate')}")
                elif key == "litigations":
                    print(f"    {i}. {item['case_name']}: {item.get('status')}")
                elif key == "executive_compensations":
                    print(f"    {i}. {item['name']} ({item['title']}): total={item.get('total_comp')}")
                elif key == "related_party_transactions":
                    print(f"    {i}. {item['related_party']} ({item['relationship']}): {item.get('transaction_type')}, amt={item.get('amount')}")
                else:
                    print(f"    {i}. {item.get('item_key', '?')}: {item.get('value', '?')}")
            if len(items) > 5:
                print(f"    ... 还有 {len(items) - 5} 条")

    print(f"\n完整 JSON 已保存到 {out}")

    # 覆盖率检查
    print("\n" + "=" * 60)
    print("覆盖率检查")
    print("=" * 60)
    covered = sum(1 for _, key in sections if data.get(key))
    total = len(sections)
    print(f"  {covered}/{total} 个表有数据")
    empty = [label for label, key in sections if not data.get(key)]
    if empty:
        print(f"  空表（原文未包含）: {', '.join(empty)}")


if __name__ == "__main__":
    asyncio.run(main())
