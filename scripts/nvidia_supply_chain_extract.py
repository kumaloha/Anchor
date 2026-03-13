"""NVIDIA 多年 10-K 上下游深度提取 — 客户集中度 + 供应链详情"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

FISCAL_YEARS = ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]

SYSTEM = """\
你是一位资深供应链和商业分析师。从 10-K 年报中深度提取上下游商业关系的全部细节。

## 输出格式
```json
{
  "customer_concentration": [
    {
      "customer_name": "具名客户名 或 'Customer A' 等匿名标识",
      "customer_type": "direct|indirect",
      "revenue_pct": null,
      "revenue_amount": null,
      "segment": "归属业务分部",
      "note": "补充说明（如跨年变化、匿名身份线索）"
    }
  ],
  "downstream_details": [
    {
      "segment_name": "业务分部名",
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "",
      "products": "具体产品线（全部列出，不要概括）",
      "channels": "销售渠道: OEM/ODM/AIB/分销商/直销/云市场",
      "end_customers": "终端客户类型 + 具名客户",
      "pricing_model": "一次性销售|订阅|授权|NRE|混合",
      "asp_trend": "ASP变化趋势（如有）",
      "backlog": "在手订单/积压情况（如有）",
      "description": "补充说明"
    }
  ],
  "upstream_details": [
    {
      "supplier_name": "供应商名（每个供应商单独一行，不要合并）",
      "supply_type": "foundry|assembly_test|memory|substrate|component|contract_mfg|software|logistics",
      "material_or_service": "具体供应内容",
      "process_node": "制程节点（如适用，如 5nm/4nm）",
      "geographic_location": "供应商所在地/工厂所在地",
      "is_sole_source": "是否独家供应",
      "lead_time": "交货周期（如有）",
      "contract_type": "长期合约|purchase order|prepaid|non-cancellable",
      "prepaid_amount": null,
      "purchase_obligations": null,
      "concentration_risk": "集中度风险描述",
      "note": "补充说明"
    }
  ],
  "purchase_obligations_summary": {
    "total_outstanding": null,
    "inventory_purchase_obligations": null,
    "non_inventory_obligations": null,
    "cloud_service_agreements": null,
    "breakdown_by_year": [
      {"year": "FY20XX", "amount": null}
    ],
    "note": ""
  },
  "inventory_provisions": {
    "provision_amount": null,
    "provision_release": null,
    "net_margin_impact_pct": null,
    "note": ""
  }
}
```

## 提取规则
1. **每个供应商单独一行**，不要把 "SK Hynix, Micron, Samsung" 合并
2. **客户集中度**：从 revenue concentration 段落提取，包括匿名的 "Customer A/B/C" 及其收入占比
3. **产品线要具体**：不要写"GPUs"，要写"GeForce RTX 4090/4080/4070 Ti, GeForce RTX 3060"
4. **采购义务**：从 purchase obligations / commitments 段落提取总金额和分年明细
5. **库存减值**：从 inventory provisions 段落提取减值金额和毛利率影响
6. **交货周期**：原文提到的 lead time（如"exceeding twelve months"）要提取
7. **预付/合约**：prepaid manufacturing agreements、non-cancellable orders 的金额
8. 金额单位统一为百万美元
9. 只输出 JSON
"""


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def extract_one(fy: str) -> tuple[str, dict | None]:
    path = Path(f"/tmp/nvidia_10k_{fy}.txt")
    content = path.read_text()
    user = f"请从以下 NVIDIA {fy.upper()} 10-K 中深度提取上下游商业关系：\n\n{content}"

    print(f"[{fy}] 调用 LLM... ({len(content):,} chars)")
    try:
        resp = await chat_completion(system=SYSTEM, user=user, max_tokens=16384)
        if not resp:
            print(f"[{fy}] FAILED")
            return fy, None
        print(f"[{fy}] 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        data = clean_json(resp.content)
        Path(f"/tmp/nvidia_supply_chain_{fy}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        return fy, data
    except Exception as e:
        print(f"[{fy}] ERROR: {e}")
        return fy, None


async def main():
    sem = asyncio.Semaphore(3)

    async def limited(fy):
        async with sem:
            return await extract_one(fy)

    results = await asyncio.gather(*[limited(fy) for fy in FISCAL_YEARS])
    all_data = {fy: data for fy, data in results if data}

    Path("/tmp/nvidia_supply_chain_all.json").write_text(
        json.dumps(all_data, ensure_ascii=False, indent=2)
    )

    # ── 打印报告 ──
    lines = []
    lines.append("=" * 110)
    lines.append("NVIDIA 上下游深度提取报告")
    lines.append("=" * 110)

    # 客户集中度
    lines.append("\n" + "=" * 110)
    lines.append("【客户集中度】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        custs = d.get("customer_concentration", [])
        lines.append(f"\n  ── {fy.upper()} ({len(custs)} 条) ──")
        for c in custs:
            pct = f"{c['revenue_pct']}%" if c.get("revenue_pct") else "?"
            lines.append(
                f"    {c.get('customer_name', '?')} [{c.get('customer_type', '?')}]: "
                f"{pct} of revenue ({c.get('segment', '?')})"
            )
            if c.get("note"):
                lines.append(f"      → {c['note'][:120]}")

    # 下游详情
    lines.append("\n" + "=" * 110)
    lines.append("【下游详情】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        segs = d.get("downstream_details", [])
        lines.append(f"\n  ── {fy.upper()} ({len(segs)} 分部) ──")
        for s in segs:
            rev = f"${s['revenue']:,.0f}M" if s.get("revenue") else "?"
            lines.append(f"    【{s.get('segment_name', '?')}】 rev={rev}")
            if s.get("products"):
                lines.append(f"      产品: {s['products'][:150]}")
            if s.get("channels"):
                lines.append(f"      渠道: {s['channels'][:150]}")
            if s.get("end_customers"):
                lines.append(f"      客户: {s['end_customers'][:150]}")
            if s.get("pricing_model"):
                lines.append(f"      定价: {s['pricing_model']}")
            if s.get("asp_trend"):
                lines.append(f"      ASP: {s['asp_trend'][:100]}")

    # 上游详情
    lines.append("\n" + "=" * 110)
    lines.append("【上游详情】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        sups = d.get("upstream_details", [])
        lines.append(f"\n  ── {fy.upper()} ({len(sups)} 供应商) ──")
        for s in sups:
            sole = " [独家]" if s.get("is_sole_source") else ""
            lines.append(
                f"    {s.get('supplier_name', '?')} [{s.get('supply_type', '?')}]{sole}"
            )
            lines.append(f"      供应: {s.get('material_or_service', '?')}")
            if s.get("process_node"):
                lines.append(f"      制程: {s['process_node']}")
            if s.get("geographic_location"):
                lines.append(f"      地点: {s['geographic_location']}")
            if s.get("lead_time"):
                lines.append(f"      交期: {s['lead_time']}")
            if s.get("contract_type"):
                lines.append(f"      合约: {s['contract_type']}")
            if s.get("prepaid_amount"):
                lines.append(f"      预付: ${s['prepaid_amount']}M")
            if s.get("concentration_risk"):
                lines.append(f"      风险: {s['concentration_risk'][:120]}")

    # 采购义务
    lines.append("\n" + "=" * 110)
    lines.append("【采购义务总览】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        po = d.get("purchase_obligations_summary", {})
        total = f"${po['total_outstanding']:,.0f}M" if po.get("total_outstanding") else "?"
        inv = f"${po['inventory_purchase_obligations']:,.0f}M" if po.get("inventory_purchase_obligations") else "?"
        lines.append(f"  {fy.upper()}: total={total}, inventory={inv}")
        if po.get("breakdown_by_year"):
            for b in po["breakdown_by_year"]:
                lines.append(f"    {b.get('year', '?')}: ${b.get('amount', '?')}M")

    # 库存减值
    lines.append("\n" + "=" * 110)
    lines.append("【库存减值】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        ip = d.get("inventory_provisions", {})
        prov = f"${ip['provision_amount']:,.0f}M" if ip.get("provision_amount") else "?"
        impact = f"{ip['net_margin_impact_pct']}%" if ip.get("net_margin_impact_pct") else "?"
        lines.append(f"  {fy.upper()}: provision={prov}, margin_impact={impact}")

    output = "\n".join(lines)
    out_path = Path("/tmp/nvidia_supply_chain_report.txt")
    out_path.write_text(output)
    print("\n" + output)
    print(f"\n已保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
