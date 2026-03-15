"""NVIDIA 多年 10-K 深度提取 — 6 主题 × 5 财年 并行
每个主题用专用深度 prompt，替代旧的单一通用 prompt。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from anchor.llm_client import chat_completion

FISCAL_YEARS = ["fy2021", "fy2022", "fy2023", "fy2024", "fy2025"]
OUT_DIR = Path("/tmp/nvidia_deep")


def clean_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text)


# ============================================================
# 主题 1: 供应链（上下游 + 客户集中度 + 采购义务）
# ============================================================
PROMPT_SUPPLY_CHAIN = """\
你是一位资深供应链和商业分析师。从 10-K 年报中深度提取上下游商业关系的全部细节。

## 输出格式
```json
{
  "customer_concentration": [
    {
      "customer_name": "具名客户名 或 'Customer A' 等匿名标识",
      "customer_type": "direct|indirect|channel|OEM|distributor",
      "revenue_pct": null,
      "revenue_amount": null,
      "segment": "归属业务分部（如能归属）或 null",
      "products": "卖给该客户的产品/服务",
      "channels": "销售渠道",
      "backlog": null,
      "backlog_note": "",
      "pricing_model": "一次性销售|订阅|授权|NRE|混合",
      "contract_duration": "one-time|1-year|multi-year",
      "note": "补充说明（如跨年变化、匿名身份线索）"
    }
  ],
  "upstream_details": [
    {
      "supplier_name": "供应商名（每个供应商单独一行，不要合并）",
      "supply_type": "foundry|assembly_test|memory|substrate|component|contract_mfg|software|logistics",
      "material_or_service": "具体供应内容",
      "segment": "归属业务分部（如能归属）或 null",
      "process_node": "制程节点（如适用，如 5nm/4nm）",
      "geographic_location": "供应商所在地/工厂所在地",
      "is_sole_source": false,
      "lead_time": "交货周期（如有）",
      "contract_type": "长期合约|purchase_order|prepaid|non-cancellable",
      "prepaid_amount": null,
      "purchase_obligation": null,
      "concentration_risk": "集中度风险描述",
      "note": "补充说明"
    }
  ],
  "purchase_obligations_summary": {
    "total_outstanding": null,
    "inventory_purchase_obligations": null,
    "non_inventory_obligations": null,
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
3. **segment 字段**：如果原文能归属到具体业务线就填（如 "Compute & Networking"），不能就填 null
4. **采购义务**：从 purchase obligations / commitments 段落提取总金额和分年明细
5. **库存减值**：从 inventory provisions 段落提取减值金额和毛利率影响
6. **交货周期**：原文提到的 lead time 要提取
7. **预付/合约**：prepaid manufacturing agreements、non-cancellable orders 的金额
8. 金额单位统一为百万美元
9. 只输出 JSON
"""

# ============================================================
# 主题 2: 收入模式
# ============================================================
PROMPT_REVENUE_MODEL = """\
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

# ============================================================
# 主题 3: 财务细节（债务 + 权益 + 税务 + SBC）
# ============================================================
PROMPT_FINANCIALS = """\
你是一位资深财务分析师。从 10-K 年报中深度提取财务细节信息。

## 输出格式
```json
{
  "debt_obligations": [
    {
      "instrument_name": "债务工具全名（如 1.55% Notes due 2028）",
      "debt_type": "bond|loan|lease|convertible|credit_facility|commercial_paper",
      "principal": null,
      "interest_rate": null,
      "maturity_date": "YYYY-MM-DD 或 YYYY",
      "is_secured": false,
      "covenants": "重要限制条款",
      "note": ""
    }
  ],
  "equity_items": [
    {
      "item_key": "标准化键（如 share_repurchase, dividends_paid, shares_outstanding）",
      "item_label": "原始标签",
      "value": 0,
      "note": ""
    }
  ],
  "tax_items": [
    {
      "item_key": "标准化键（如 effective_tax_rate, deferred_tax_asset, tax_provision）",
      "item_label": "原始标签",
      "value": 0,
      "note": ""
    }
  ],
  "sbc_items": [
    {
      "item_key": "标准化键（如 total_sbc, rsu_granted, espp_expense）",
      "item_label": "原始标签",
      "value": 0,
      "note": ""
    }
  ]
}
```

## 提取规则
1. **债务要逐笔列出** — 每个 Notes/Bonds 单独一行，含利率和到期日
2. **权益变动** — 回购金额、分红金额、流通股数等
3. **税务** — 有效税率、递延税资产/负债、税务拨备
4. **SBC** — 股权激励总费用、RSU/ESPP/Option 分项
5. 金额单位统一为百万美元
6. 只输出 JSON
"""

# ============================================================
# 主题 4: 经营议题 + 叙事
# ============================================================
PROMPT_OPERATIONS = """\
你是一位资深基本面分析师。从 10-K 年报中提取管理层的经营议题讨论和战略叙事。

## 输出格式
```json
{
  "operational_issues": [
    {
      "topic": "议题名 ≤30字（如'数据中心AI需求爆发'、'中国出口管制影响'、'供应链产能约束'）",
      "performance": "管理层对该议题的定性描述（不含财务数字）≤200字",
      "attribution": "为什么出现这个表现（归因）≤200字",
      "risk": "该议题面临什么风险 ≤200字",
      "guidance": "管理层对未来的展望/指引 ≤200字"
    }
  ],
  "narratives": [
    {
      "narrative": "管理层讲的故事/战略承诺 ≤300字（如'加速计算是下一个计算范式'）",
      "capital_required": null,
      "capital_unit": "USD_millions",
      "promised_outcome": "承诺的结果 ≤200字",
      "deadline": null
    }
  ]
}
```

## 提取规则
1. 从 CEO致股东信、MD&A 等定性讨论段落提取
2. **每行 = 一个独立经营议题**，不要合并（如"AI需求"和"供应链管理"是两个议题）
3. performance 是定性描述，不要放财务数字
4. 四个字段都是 Optional，没提到就留 null
5. narratives 是管理层的战略故事和长期承诺，不是财务数字
6. 通常每个财年有 4-8 个经营议题 + 2-5 个叙事
7. 只输出 JSON
"""

# ============================================================
# 主题 5: 诉讼 + 非财务 KPI
# ============================================================
PROMPT_RISK_KPI = """\
你是一位资深风险分析师。从 10-K 年报中提取诉讼/或有事项和非财务KPI。

## 输出格式
```json
{
  "litigations": [
    {
      "case_name": "案件名称（如 'Securities Class Action Lawsuit'）",
      "case_type": "lawsuit|regulatory|patent|antitrust|environmental|tax|securities|derivative|other",
      "status": "pending|settled|dismissed|ongoing|appealed",
      "counterparty": "对手方",
      "amount_claimed": null,
      "amount_accrued": null,
      "description": "案情摘要 ≤200字"
    }
  ],
  "non_financial_kpis": [
    {
      "kpi_name": "指标名称（如 'Total Employees', 'R&D as % of Revenue'）",
      "kpi_value": "值",
      "kpi_unit": "单位",
      "yoy_change": "同比变化",
      "category": "workforce|customer|product|esg|operational"
    }
  ]
}
```

## 提取规则
1. **每个诉讼单独一行** — 从 Legal Proceedings / Note on Commitments and Contingencies 提取
2. 包括：证券集体诉讼、专利诉讼、监管调查、反垄断、衍生诉讼等
3. **非财务KPI** — 员工人数、研发占比、专利数、客户数、ESG指标等
4. 从 MD&A、Risk Factors、Human Capital 等段落提取
5. 金额单位统一为百万美元
6. 只输出 JSON
"""

# ============================================================
# 主题 6: 地理收入
# ============================================================
PROMPT_GEOGRAPHIC = """\
你是一位资深财务分析师。从 10-K 年报中提取地域收入分布的全部细节。

## 输出格式
```json
{
  "geographic_revenues": [
    {
      "region": "地域名称（按原文分类，如 United States, Taiwan, China, Other Asia Pacific, Europe, Other）",
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "同比增速",
      "note": "补充说明（如出口管制影响、大客户所在地等）"
    }
  ],
  "geographic_risk_notes": [
    {
      "region": "相关地域",
      "risk": "风险描述（如出口管制、地缘政治、汇率）"
    }
  ]
}
```

## 提取规则
1. 从 Revenue by Geographic Area (Note) 提取每个地域的收入和占比
2. 保留原文的地域分类（不要自己合并 Taiwan 和 China）
3. 如有同比数据则计算 growth_yoy
4. 从 Risk Factors 中提取地域相关风险
5. 金额单位统一为百万美元
6. 只输出 JSON
"""

# ============================================================
# 主题注册表
# ============================================================
TOPICS = {
    "supply_chain": PROMPT_SUPPLY_CHAIN,
    "revenue_model": PROMPT_REVENUE_MODEL,
    "financials": PROMPT_FINANCIALS,
    "operations": PROMPT_OPERATIONS,
    "risk_kpi": PROMPT_RISK_KPI,
    "geographic": PROMPT_GEOGRAPHIC,
}


async def extract_topic_fy(
    topic: str, prompt: str, fy: str,
    sections_cache: dict[str, dict[str, str]],
) -> tuple[str, str, dict | None]:
    """提取一个主题的一个财年（使用分段后的相关段落）"""
    from sec_10k_splitter import split_10k, get_sections_for_topic

    # 懒加载并缓存分段结果
    if fy not in sections_cache:
        full_text = Path(f"/tmp/nvidia_10k_{fy}.txt").read_text()
        sections_cache[fy] = split_10k(full_text)

    content = get_sections_for_topic(topic, sections_cache[fy])
    if not content:
        # fallback: 用全文
        content = Path(f"/tmp/nvidia_10k_{fy}.txt").read_text()

    user = f"请从以下 NVIDIA {fy.upper()} 10-K 相关段落中提取信息：\n\n{content}"

    tag = f"[{topic}/{fy}]"
    print(f"{tag} 调用 LLM... ({len(content):,} chars)")
    try:
        resp = await chat_completion(system=prompt, user=user, max_tokens=16384)
        if not resp:
            print(f"{tag} FAILED — 无响应")
            return topic, fy, None
        print(f"{tag} 完成 — {resp.input_tokens:,} in / {resp.output_tokens:,} out")
        data = clean_json(resp.content)
        return topic, fy, data
    except Exception as e:
        print(f"{tag} ERROR: {e}")
        return topic, fy, None


async def main():
    OUT_DIR.mkdir(exist_ok=True)

    # 分段缓存（每个财年只分段一次）
    sections_cache: dict[str, dict[str, str]] = {}

    # 6 主题 × 5 财年 = 30 个任务，限制并发 5
    sem = asyncio.Semaphore(5)

    async def limited(topic, prompt, fy):
        async with sem:
            return await extract_topic_fy(topic, prompt, fy, sections_cache)

    tasks = []
    for topic, prompt in TOPICS.items():
        for fy in FISCAL_YEARS:
            tasks.append(limited(topic, prompt, fy))

    results = await asyncio.gather(*tasks)

    # 按主题聚合
    by_topic: dict[str, dict[str, dict]] = {}
    for topic, fy, data in results:
        if data:
            by_topic.setdefault(topic, {})[fy] = data

    # 保存每个主题的聚合结果
    for topic, all_data in by_topic.items():
        out_path = OUT_DIR / f"{topic}.json"
        out_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2))
        print(f"\n已保存 {topic}: {len(all_data)} 个财年 → {out_path}")

    # 汇总统计
    print("\n" + "=" * 80)
    print("深度提取汇总")
    print("=" * 80)
    for topic in TOPICS:
        data = by_topic.get(topic, {})
        fy_list = sorted(data.keys())
        print(f"  {topic}: {len(fy_list)} 财年 — {', '.join(f.upper() for f in fy_list)}")

        # 打印每个主题每个财年的条目数
        for fy in fy_list:
            d = data[fy]
            counts = []
            for k, v in d.items():
                if isinstance(v, list):
                    counts.append(f"{k}={len(v)}")
                elif isinstance(v, dict) and any(v.values()):
                    counts.append(f"{k}=✓")
            print(f"    {fy.upper()}: {', '.join(counts)}")


if __name__ == "__main__":
    asyncio.run(main())
