"""NVIDIA 多年 Proxy Statement (DEF 14A) 并行提取 — 管理层薪酬 + 关联交易"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

FISCAL_YEARS = ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]

SYSTEM = """\
你是一位资深基本面分析师。从公司 Proxy Statement (DEF 14A) 中提取以下结构化信息。

## 输出格式
输出一个 JSON 对象。如果原文中没有某类信息，对应字段返回空数组 []。

```json
{
  "executive_compensations": [
    {
      "name": "姓名",
      "title": "职位",
      "fiscal_year": "FY20XX",
      "base_salary": null,
      "bonus": null,
      "stock_awards": null,
      "option_awards": null,
      "non_equity_incentive": null,
      "other_comp": null,
      "total_comp": null
    }
  ],
  "director_compensations": [
    {
      "name": "姓名",
      "fees_earned_cash": null,
      "stock_awards": null,
      "option_awards": null,
      "all_other": null,
      "total": null
    }
  ],
  "related_party_transactions": [
    {
      "related_party": "关联方名称",
      "relationship": "director|officer|major_shareholder|subsidiary|affiliate|family",
      "transaction_type": "employment|sale|purchase|lease|loan|guarantee|service|license|other",
      "amount": null,
      "description": "交易说明"
    }
  ],
  "stock_ownership": [
    {
      "name": "姓名",
      "title": "职位/身份",
      "shares_beneficially_owned": null,
      "percent_of_class": null
    }
  ],
  "ceo_pay_ratio": {
    "ceo_total": null,
    "median_employee_total": null,
    "ratio": null
  }
}
```

## 规则
1. 金额单位为美元（原始值，不做换算）
2. 从 Summary Compensation Table 提取 NEO 薪酬，注意该表通常包含多个财年数据——全部提取
3. 从 Director Compensation Table 提取董事薪酬
4. Related Party Transactions / Certain Relationships 章节提取关联交易
5. Security Ownership 章节提取持股信息（只提取 >1% 或 named individuals）
6. CEO Pay Ratio 章节提取薪酬比
7. 只输出 JSON
"""


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


async def extract_one(fy: str) -> tuple[str, dict | None]:
    path = Path(f"/tmp/nvidia_proxy_{fy}.txt")
    content = path.read_text()
    user = f"请从以下 NVIDIA {fy.upper()} Proxy Statement 中提取全部结构化信息：\n\n{content}"

    print(f"[{fy}] 调用 LLM... ({len(content):,} chars)")
    try:
        resp = await chat_completion(system=SYSTEM, user=user, max_tokens=16384)
        if not resp:
            print(f"[{fy}] FAILED - no response")
            return fy, None
        print(f"[{fy}] 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        data = clean_json(resp.content)
        Path(f"/tmp/nvidia_proxy_{fy}_extract.json").write_text(
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

    Path("/tmp/nvidia_proxy_multiyear_extract.json").write_text(
        json.dumps(all_data, ensure_ascii=False, indent=2)
    )

    # ── 打印报告 ──
    lines = []
    lines.append("=" * 110)
    lines.append("NVIDIA 多年 Proxy Statement 提取对比报告")
    lines.append("=" * 110)

    # ── 1. 管理层薪酬 ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【管理层薪酬 (Summary Compensation Table)】")
    lines.append("=" * 110)

    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        comps = d.get("executive_compensations", [])
        lines.append(f"\n  ── {fy.upper()} Proxy ({len(comps)} 条) ──")
        if not comps:
            lines.append("    （无数据）")
            continue

        # group by fiscal year within proxy
        by_year = {}
        for c in comps:
            yr = c.get("fiscal_year", fy)
            by_year.setdefault(yr, []).append(c)

        for yr in sorted(by_year.keys()):
            lines.append(f"    [{yr}]")
            lines.append(f"    {'姓名':<25} {'基薪':>12} {'股权':>14} {'现金奖金':>12} {'其他':>12} {'总计':>14}")
            lines.append(f"    {'-'*25} {'-'*12} {'-'*14} {'-'*12} {'-'*12} {'-'*14}")
            for c in by_year[yr]:
                name = c.get("name", "?")[:24]
                salary = f"${c['base_salary']:,.0f}" if c.get("base_salary") else "-"
                stock = f"${c['stock_awards']:,.0f}" if c.get("stock_awards") else "-"
                cash = f"${c['non_equity_incentive']:,.0f}" if c.get("non_equity_incentive") else "-"
                other = f"${c['other_comp']:,.0f}" if c.get("other_comp") else "-"
                total = f"${c['total_comp']:,.0f}" if c.get("total_comp") else "-"
                lines.append(f"    {name:<25} {salary:>12} {stock:>14} {cash:>12} {other:>12} {total:>14}")

    # ── 2. CEO薪酬趋势 ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【CEO 薪酬趋势】")
    lines.append("=" * 110)
    lines.append(f"    {'财年':<10} {'基薪':>12} {'股权':>14} {'现金奖金':>12} {'总计':>14}")
    lines.append(f"    {'-'*10} {'-'*12} {'-'*14} {'-'*12} {'-'*14}")
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        comps = d.get("executive_compensations", [])
        # find CEO entry for the proxy's own fiscal year
        ceo = None
        for c in comps:
            if "huang" in c.get("name", "").lower() and fy.replace("fy", "FY") in str(c.get("fiscal_year", "")):
                ceo = c
                break
        if not ceo:
            # fallback: first Huang entry
            for c in comps:
                if "huang" in c.get("name", "").lower():
                    ceo = c
                    break
        if ceo:
            salary = f"${ceo['base_salary']:,.0f}" if ceo.get("base_salary") else "-"
            stock = f"${ceo['stock_awards']:,.0f}" if ceo.get("stock_awards") else "-"
            cash = f"${ceo['non_equity_incentive']:,.0f}" if ceo.get("non_equity_incentive") else "-"
            total = f"${ceo['total_comp']:,.0f}" if ceo.get("total_comp") else "-"
            lines.append(f"    {fy.upper():<10} {salary:>12} {stock:>14} {cash:>12} {total:>14}")
        else:
            lines.append(f"    {fy.upper():<10} （无数据）")

    # ── 3. 董事薪酬 ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【董事薪酬】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        dirs = d.get("director_compensations", [])
        lines.append(f"\n  ── {fy.upper()} ({len(dirs)} 人) ──")
        if not dirs:
            lines.append("    （无数据）")
            continue
        for dr in dirs:
            name = dr.get("name", "?")[:30]
            total = f"${dr['total']:,.0f}" if dr.get("total") else "?"
            lines.append(f"    {name}: total={total}")

    # ── 4. 关联交易 ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【关联交易】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        rpts = d.get("related_party_transactions", [])
        lines.append(f"\n  ── {fy.upper()} ({len(rpts)} 条) ──")
        if not rpts:
            lines.append("    （无数据）")
            continue
        for i, r in enumerate(rpts, 1):
            amt = f"${r['amount']:,.0f}" if r.get("amount") else "?"
            lines.append(
                f"    {i}. {r.get('related_party', '?')} ({r.get('relationship', '?')}): "
                f"{r.get('transaction_type', '?')}, amt={amt}"
            )
            if r.get("description"):
                lines.append(f"       {r['description'][:150]}")

    # ── 5. 持股 ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【主要持股人】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        owners = d.get("stock_ownership", [])
        lines.append(f"\n  ── {fy.upper()} ({len(owners)} 人) ──")
        if not owners:
            lines.append("    （无数据）")
            continue
        for o in owners[:10]:
            pct = f"{o['percent_of_class']:.2f}%" if o.get("percent_of_class") else "?"
            shares = f"{o['shares_beneficially_owned']:,.0f}" if o.get("shares_beneficially_owned") else "?"
            lines.append(f"    {o.get('name', '?')}: {shares} shares ({pct})")

    # ── 6. CEO Pay Ratio ──
    lines.append("")
    lines.append("=" * 110)
    lines.append("【CEO Pay Ratio】")
    lines.append("=" * 110)
    for fy in FISCAL_YEARS:
        d = all_data.get(fy, {})
        pr = d.get("ceo_pay_ratio", {})
        if pr and pr.get("ratio"):
            lines.append(
                f"    {fy.upper()}: CEO=${pr.get('ceo_total', '?'):,} / "
                f"Median=${pr.get('median_employee_total', '?'):,} → "
                f"Ratio={pr.get('ratio')}:1"
            )
        else:
            lines.append(f"    {fy.upper()}: （无数据）")

    output = "\n".join(lines)
    out_path = Path("/tmp/nvidia_proxy_multiyear_report.txt")
    out_path.write_text(output)
    print("\n" + output)
    print(f"\n已保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
