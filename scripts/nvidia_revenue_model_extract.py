"""NVIDIA 多年 10-K 收入模式深度提取"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

FISCAL_YEARS = ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]

SYSTEM = """\
你是一位资深商业模式分析师。从 10-K 年报中深度提取收入模式（Revenue Model）的全部细节。

## 输出格式
```json
{
  "revenue_streams": [
    {
      "stream_name": "收入流名称（如 Data Center GPU Hardware, GeForce NOW Subscription）",
      "segment": "归属业务分部",
      "revenue_type": "product_sale|subscription|license|royalty|service|NRE|cloud_service|advertising",
      "recognition_method": "point_in_time|over_time",
      "is_recurring": false,
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "",
      "pricing_model": "定价方式描述（如 per-unit, per-GPU, per-user/month, usage-based）",
      "contract_duration": "合约周期（如 one-time, 1-year, 3-year, multi-year）",
      "deferred_revenue_related": false,
      "description": "补充说明"
    }
  ],
  "recurring_vs_nonrecurring": {
    "recurring_revenue": null,
    "recurring_pct": null,
    "nonrecurring_revenue": null,
    "nonrecurring_pct": null,
    "note": "如原文无明确拆分，说明估算依据"
  },
  "deferred_revenue": {
    "total_deferred": null,
    "short_term": null,
    "long_term": null,
    "recognized_in_period": null,
    "note": ""
  },
  "revenue_recognition_policies": [
    {
      "category": "product|software_license|subscription|service|NRE",
      "policy": "确认方式描述（时点/时段、交付条件、多要素安排等）",
      "key_judgments": "涉及的关键判断（如SSP估计、合约组合、可变对价）"
    }
  ],
  "software_and_services_detail": [
    {
      "product_name": "软件/服务名称",
      "type": "subscription|perpetual_license|cloud_service|support|professional_service",
      "pricing": "定价方式",
      "revenue": null,
      "note": "补充说明"
    }
  ],
  "asp_and_pricing_trends": [
    {
      "product_category": "产品类别",
      "trend": "ASP变化趋势",
      "driver": "驱动因素",
      "note": ""
    }
  ]
}
```

## 提取规则
1. **每个独立收入流单独一行** — 不要把 GPU Hardware 和 Software License 合并
2. **区分 recurring vs non-recurring** — subscription/cloud_service/license 是 recurring，product_sale 是 non-recurring
3. **软件和服务收入要细分** — NVIDIA AI Enterprise, vGPU, GeForce NOW, DGX Cloud, Omniverse 等分别列出
4. **从 Note 1 (Revenue Recognition) 提取确认政策** — 原文会详细说明 product vs software vs service 的确认方式
5. **Deferred Revenue** — 从资产负债表或注释提取递延收入余额
6. **ASP趋势** — 从MD&A中提取任何关于定价/ASP变化的讨论
7. 金额单位统一为百万美元
8. 只输出 JSON
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
    user = f"请从以下 NVIDIA {fy.upper()} 10-K 中深度提取收入模式信息：\n\n{content}"

    print(f"[{fy}] 调用 LLM... ({len(content):,} chars)")
    try:
        resp = await chat_completion(system=SYSTEM, user=user, max_tokens=16384)
        if not resp:
            print(f"[{fy}] FAILED")
            return fy, None
        print(f"[{fy}] 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        data = clean_json(resp.content)
        Path(f"/tmp/nvidia_revenue_model_{fy}.json").write_text(
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

    Path("/tmp/nvidia_revenue_model_all.json").write_text(
        json.dumps(all_data, ensure_ascii=False, indent=2)
    )

    # ── 打印报告 ──
    lines = []
    lines.append("=" * 110)
    lines.append("NVIDIA 收入模式深度分析报告")
    lines.append("=" * 110)

    # 收入流
    lines.append("\n" + "=" * 110)
    lines.append("【收入流明细】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        streams = d.get("revenue_streams", [])
        lines.append(f"\n  ── {fy.upper()} ({len(streams)} 个收入流) ──")
        for s in streams:
            rev = f"${s['revenue']:,.0f}M" if s.get("revenue") else ""
            recur = " [recurring]" if s.get("is_recurring") else ""
            lines.append(
                f"    {s.get('stream_name', '?')}{recur}"
            )
            lines.append(
                f"      类型: {s.get('revenue_type', '?')} | "
                f"确认: {s.get('recognition_method', '?')} | "
                f"定价: {s.get('pricing_model', '?')}"
            )
            if rev:
                lines.append(f"      收入: {rev}")
            if s.get("contract_duration"):
                lines.append(f"      合约: {s['contract_duration']}")

    # Recurring vs Non-recurring
    lines.append("\n" + "=" * 110)
    lines.append("【Recurring vs Non-recurring】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        rr = d.get("recurring_vs_nonrecurring", {})
        rec_pct = f"{rr['recurring_pct']*100:.1f}%" if rr.get("recurring_pct") and isinstance(rr['recurring_pct'], (int, float)) else str(rr.get("recurring_pct", "?"))
        lines.append(f"  {fy.upper()}: recurring={rr.get('recurring_revenue', '?')}M ({rec_pct})")
        if rr.get("note"):
            lines.append(f"    → {rr['note'][:150]}")

    # Deferred Revenue
    lines.append("\n" + "=" * 110)
    lines.append("【递延收入 Deferred Revenue】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        dr = d.get("deferred_revenue", {})
        total = f"${dr['total_deferred']:,.0f}M" if dr.get("total_deferred") else "?"
        short = f"${dr['short_term']:,.0f}M" if dr.get("short_term") else "?"
        long = f"${dr['long_term']:,.0f}M" if dr.get("long_term") else "?"
        lines.append(f"  {fy.upper()}: total={total} (short={short}, long={long})")

    # Software & Services
    lines.append("\n" + "=" * 110)
    lines.append("【软件和服务收入明细】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        sw = d.get("software_and_services_detail", [])
        lines.append(f"\n  ── {fy.upper()} ({len(sw)} 项) ──")
        if not sw:
            lines.append("    （无数据）")
        for s in sw:
            rev = f"${s['revenue']:,.0f}M" if s.get("revenue") else ""
            lines.append(
                f"    {s.get('product_name', '?')} [{s.get('type', '?')}] "
                f"{s.get('pricing', '')} {rev}"
            )

    # ASP Trends
    lines.append("\n" + "=" * 110)
    lines.append("【ASP/定价趋势】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        asp = d.get("asp_and_pricing_trends", [])
        lines.append(f"\n  ── {fy.upper()} ({len(asp)} 项) ──")
        for a in asp:
            lines.append(
                f"    {a.get('product_category', '?')}: {a.get('trend', '?')}"
            )
            if a.get("driver"):
                lines.append(f"      驱动: {a['driver'][:120]}")

    # Revenue Recognition Policies
    lines.append("\n" + "=" * 110)
    lines.append("【收入确认政策（最新年份）】")
    lines.append("=" * 110)
    latest = all_data.get("fy2025", {})
    policies = latest.get("revenue_recognition_policies", [])
    for p in policies:
        lines.append(f"  [{p.get('category', '?')}]")
        lines.append(f"    政策: {p.get('policy', '?')[:200]}")
        if p.get("key_judgments"):
            lines.append(f"    判断: {p['key_judgments'][:150]}")

    output = "\n".join(lines)
    out_path = Path("/tmp/nvidia_revenue_model_report.txt")
    out_path.write_text(output)
    print("\n" + output)
    print(f"\n已保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
