"""
pipelines/company.py — Company 专用提取管线
============================================
直接提取 13 张 company 表数据，绕过 Node/Edge 架构。

架构：
  extract_company_compute(content, platform, author, today) → CompanyComputeResult
  extract_company_write(raw_post, session, compute_result) → dict
  extract_company() — 串行包装
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.pipelines._base import call_llm, parse_json, safe_float, safe_str
from anchor.extract.schemas.company import CompanyExtractionResult
from anchor.models import (
    AuditOpinion,
    CompanyNarrative,
    CompanyProfile,
    CompetitorRelation,
    DebtObligation,
    DownstreamSegment,
    ExecutiveChange,
    ExecutiveCompensation,
    FinancialLineItem,
    FinancialStatement,
    GeographicRevenue,
    KnownIssue,
    Litigation,
    ManagementAcknowledgment,
    ManagementGuidance,
    MarketShareData,
    NonFinancialKPI,
    OperationalIssue,
    PricingAction,
    RawPost,
    RelatedPartyTransaction,
    StockOwnership,
    UpstreamSegment,
    _utcnow,
)

_MAX_TOKENS = 16384

# ── LLM 提示词 ──────────────────────────────────────────────────────────

SYSTEM_COMPANY = """\
你是一位资深基本面分析师。从公司财报/年报/Proxy Statement 中提取全部结构化信息。

## 输出格式
输出一个 JSON 对象。如果原文中没有某类信息，对应字段返回空数组 [] 或 null。

```json
{
  "is_relevant_content": true,
  "skip_reason": null,
  "company": {
    "name": "公司全名",
    "ticker": "股票代码（如 NVDA / 600519.SH）",
    "market": "us|cn_a|cn_h|hk|jp",
    "industry": "所属行业",
    "summary": "一句话商业模式"
  },
  "period": "FY2025 或 2025Q4",
  "financial_statements": {
    "currency": "USD",
    "income": [
      {"item_key": "revenue", "item_label": "原报表标签", "value": 数值, "note": null}
    ],
    "balance_sheet": [],
    "cashflow": []
  },
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
      "segment": "业务分部名称（无则 null）",
      "customer_name": "客户名或收入流名",
      "customer_type": "direct|indirect|channel|OEM|distributor",
      "products": "产品/服务",
      "channels": "销售渠道",
      "revenue": null,
      "revenue_pct": null,
      "growth_yoy": "同比增速",
      "backlog": null,
      "backlog_note": null,
      "pricing_model": "per-unit|per-user/month|usage-based|混合",
      "contract_duration": "one-time|1-year|multi-year",
      "revenue_type": "product_sale|subscription|license|royalty|service|NRE|cloud_service",
      "is_recurring": null,
      "recognition_method": "point_in_time|over_time",
      "contract_duration_months": null,
      "switching_cost_level": "high|medium|low",
      "description": "补充说明"
    }
  ],
  "upstream_segments": [
    {
      "segment": "业务分部名称（无则 null）",
      "supplier_name": "供应商名称",
      "supply_type": "foundry|memory|assembly_test|substrate|component|contract_mfg|software|logistics",
      "material_or_service": "供应内容",
      "process_node": "制程节点（如适用）",
      "geographic_location": "所在地",
      "is_sole_source": false,
      "purchase_obligation": null,
      "lead_time": null,
      "contract_type": null,
      "prepaid_amount": null,
      "concentration_risk": "集中度风险",
      "description": "补充说明"
    }
  ],
  "geographic_revenues": [
    {
      "region": "地域名称",
      "revenue": null,
      "revenue_share": null,
      "growth_yoy": "增速",
      "note": null
    }
  ],
  "non_financial_kpis": [
    {
      "kpi_name": "指标名称",
      "kpi_value": "值",
      "kpi_unit": "单位",
      "yoy_change": "变化",
      "category": "workforce|customer|product|esg|operational",
      "note": null
    }
  ],
  "debt_obligations": [
    {
      "instrument_name": "债务工具名称",
      "debt_type": "bond|loan|lease|convertible|credit_facility",
      "principal": null,
      "currency": "USD",
      "interest_rate": null,
      "maturity_date": null,
      "is_secured": false,
      "is_current": false,
      "is_floating_rate": false,
      "note": null
    }
  ],
  "litigations": [
    {
      "case_name": "案件名称",
      "case_type": "lawsuit|regulatory|patent|antitrust|environmental|tax|other",
      "status": "pending|settled|dismissed|ongoing|appealed",
      "counterparty": null,
      "filed_at": null,
      "claimed_amount": null,
      "accrued_amount": null,
      "currency": "USD",
      "description": "案情摘要"
    }
  ],
  "executive_compensations": [
    {
      "name": "姓名",
      "title": "职位",
      "role_type": "executive|director",
      "base_salary": null,
      "bonus": null,
      "stock_awards": null,
      "option_awards": null,
      "non_equity_incentive": null,
      "other_comp": null,
      "total_comp": null,
      "pay_ratio": null,
      "median_employee_comp": null
    }
  ],
  "stock_ownership": [
    {
      "name": "持有人",
      "title": "职位",
      "shares_beneficially_owned": null,
      "percent_of_class": null
    }
  ],
  "related_party_transactions": [
    {
      "related_party": "关联方名称",
      "relationship": "director|officer|major_shareholder|subsidiary|affiliate|family",
      "transaction_type": "sale|purchase|lease|loan|guarantee|service|license|other",
      "amount": null,
      "currency": "USD",
      "terms": "交易条件",
      "is_ongoing": false,
      "description": "交易说明"
    }
  ],
  "pricing_actions": [
    {
      "product_or_segment": "涉及的产品/业务线",
      "price_change_pct": null,
      "volume_impact_pct": null,
      "effective_date": "YYYY-MM-DD"
    }
  ],
  "competitor_relations": [
    {
      "competitor_name": "竞对公司名",
      "market_segment": "竞争所在细分市场",
      "relationship_type": "direct_competitor|indirect_competitor|potential_entrant"
    }
  ],
  "market_share_data": [
    {
      "company_or_competitor": "公司或竞对名",
      "market_segment": "细分市场",
      "share_pct": null,
      "source_description": "数据来源（如 IDC Q3 2025）"
    }
  ],
  "known_issues": [
    {
      "issue_description": "已知问题描述",
      "issue_category": "financial|operational|legal|reputational|regulatory",
      "severity": "critical|major|minor",
      "source_type": "analyst_report|news|litigation|financial_anomaly"
    }
  ],
  "management_acknowledgments": [
    {
      "issue_description": "管理层提及的问题",
      "response_quality": "forthright|downplay|deflect|deny",
      "has_action_plan": false
    }
  ],
  "executive_changes": [
    {
      "person_name": "姓名",
      "title": "职位",
      "change_type": "joined|departed|promoted|demoted",
      "change_date": "YYYY-MM-DD",
      "reason": "原因（可空）"
    }
  ],
  "audit_opinion": {
    "opinion_type": "unqualified|qualified|adverse|disclaimer",
    "auditor_name": "审计师事务所",
    "emphasis_matters": "强调事项（可空）"
  },
  "management_guidance": [
    {
      "target_period": "FY2026",
      "metric": "revenue_growth|operating_margin|eps|capex|roic_target|free_cash_flow|gross_margin|net_margin|tax_rate|share_repurchase|dividend|other",
      "value_low": null,
      "value_high": null,
      "unit": "pct|absolute|per_share",
      "confidence_language": "expect|target|aspire|preliminary",
      "verbatim": "原文引用"
    }
  ],
  "summary": "≤200字叙事摘要",
  "one_liner": "≤50字一句话总结"
}
```

## 财务三表提取说明（最重要）
financial_statements 提取三张财务报表的标准化科目。金额统一为百万美元（或百万本币）。
使用以下标准 item_key（只提取原文中有数据的科目）：

### 利润表 income
| item_key | 含义 |
|----------|------|
| revenue | 营业收入/总收入 |
| cost_of_revenue | 营业成本 |
| gross_profit | 毛利润 |
| operating_income | 营业利润 |
| net_income | 净利润 |
| interest_expense | 利息费用 |
| sga_expense | 销售管理费用 |
| rnd_expense | 研发费用 |
| depreciation_amortization | 折旧摊销（如单独列示） |
| eps_diluted | 稀释每股收益（单位：美元/股，非百万） |

### 资产负债表 balance_sheet
| item_key | 含义 |
|----------|------|
| total_assets | 总资产 |
| current_assets | 流动资产 |
| cash_and_equivalents | 现金及等价物 |
| accounts_receivable | 应收账款 |
| inventory | 存货 |
| goodwill | 商誉 |
| current_liabilities | 流动负债 |
| total_debt | 总有息负债（短期+长期借款+债券） |
| shareholders_equity | 股东权益 |

### 现金流量表 cashflow
| item_key | 含义 |
|----------|------|
| operating_cash_flow | 经营活动现金流 |
| capital_expenditures | 资本开支（正数表示支出） |
| depreciation_amortization | 折旧摊销 |
| dividends_paid | 已付股利（正数表示支出） |
| share_repurchase | 股票回购（正数表示支出） |
| proceeds_from_stock_issuance | 股权融资收入 |
| proceeds_from_debt_issuance | 新增借款收入 |
| basic_weighted_average_shares | 基本加权平均股数（单位：百万股） |

★ 金额全部用正数（支出也用正数），不要负号。
★ 如果原文中某科目不存在，不要猜测，直接不输出。
★ item_key 必须严格匹配上表，不要自创。

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
3. revenue_pct/revenue_share 为 0-1 比例
4. 无数据则返回空数组
5. 只输出 JSON
6. 如果文章与公司财报/年报无关，设 is_relevant_content=false 并说明原因
★ 无论原文是什么语言，summary 和 one_liner 必须使用中文。\
"""


def _build_user_message(content: str, platform: str, author: str, today: str) -> str:
    return f"""\
## 文章信息
平台：{platform}
作者：{author}
日期：{today}

## 文章内容

{content[:50000]}{"..." if len(content) > 50000 else ""}

## 提取任务

请从上述文章中提取公司财报/年报的全部结构化信息。\
"""


# ── Helper ───────────────────────────────────────────────────────────────


def _parse_date(s: str | None) -> _date | None:
    if not s or s == "null":
        return None
    s = str(s).strip()
    try:
        if len(s) == 4:
            return _date(int(s), 1, 1)
        return _date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


async def get_or_create_company(
    session: AsyncSession,
    company_data: dict | None,
) -> CompanyProfile | None:
    """根据 LLM 输出的公司信息，获取或创建 CompanyProfile。"""
    if not company_data:
        return None

    ticker = (company_data.get("ticker") or "").strip()
    if not ticker:
        return None

    result = await session.exec(
        select(CompanyProfile).where(CompanyProfile.ticker == ticker)
    )
    existing = result.first()
    if existing:
        return existing

    company = CompanyProfile(
        name=company_data.get("name", ticker),
        ticker=ticker,
        market=company_data.get("market", "us"),
        industry=company_data.get("industry"),
        summary=company_data.get("summary"),
    )
    session.add(company)
    await session.flush()
    return company


# ── Compute 阶段（纯 LLM，无 DB）────────────────────────────────────────


@dataclass
class CompanyComputeResult:
    """Company 域 LLM 提取中间结果。"""
    is_relevant: bool = False
    skip_reason: str | None = None
    data: CompanyExtractionResult | None = None


async def extract_company_compute(
    content: str,
    platform: str,
    author: str,
    today: str,
) -> CompanyComputeResult:
    """纯 LLM 计算阶段：提取 company 域全量结构化数据。"""
    result = CompanyComputeResult()

    user_msg = _build_user_message(content, platform, author, today)
    raw = await call_llm(SYSTEM_COMPANY, user_msg, _MAX_TOKENS)
    if raw is None:
        logger.warning("[Company] LLM returned None")
        return result

    parsed = parse_json(raw, CompanyExtractionResult, "company_extract")
    if parsed is None:
        logger.warning("[Company] Parse failed")
        return result

    if not parsed.is_relevant_content:
        result.skip_reason = parsed.skip_reason or "not company content"
        return result

    result.is_relevant = True
    result.data = parsed
    logger.info(
        f"[Company] Compute done: "
        f"issues={len(parsed.operational_issues)} "
        f"narratives={len(parsed.narratives)} "
        f"downstream={len(parsed.downstream_segments)} "
        f"upstream={len(parsed.upstream_segments)}"
    )
    return result


# ── Write 阶段（纯 DB，无 LLM）─────────────────────────────────────────


async def extract_company_write(
    raw_post: RawPost,
    session: AsyncSession,
    compute_result: CompanyComputeResult,
) -> dict:
    """DB 写入阶段：将 compute 结果写入 13 张 company 表。"""
    counts: dict[str, int] = {}

    if not compute_result.is_relevant or compute_result.data is None:
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        return {
            "is_relevant_content": False,
            "skip_reason": compute_result.skip_reason or "not company content",
            "table_counts": {},
            "summary": None,
        }

    data = compute_result.data

    # ── 获取或创建公司 ─────────────────────────────────────────────────
    company_dict = data.company.model_dump() if data.company else None
    company = await get_or_create_company(session, company_dict)
    if not company:
        logger.warning("[Company] Cannot identify company, skipping DB write")
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        return {
            "is_relevant_content": False,
            "skip_reason": "cannot identify company (no ticker)",
            "table_counts": {},
            "summary": data.summary,
        }

    company_id = company.id
    period = data.period or ""

    # ── Financial Statements ─────────────────────────────────────────────
    fin_item_count = 0
    if data.financial_statements:
        fs = data.financial_statements
        currency = fs.currency or "USD"
        period_type = "quarterly" if "Q" in period else "annual"

        for stmt_type, items in [
            ("income", fs.income),
            ("balance_sheet", fs.balance_sheet),
            ("cashflow", fs.cashflow),
        ]:
            if not items:
                continue
            stmt = FinancialStatement(
                company_id=company_id,
                period=period,
                period_type=period_type,
                statement_type=stmt_type,
                currency=currency,
                reported_at=raw_post.posted_at.date() if raw_post.posted_at else None,
                raw_post_id=raw_post.id,
            )
            session.add(stmt)
            await session.flush()

            for ordinal, item in enumerate(items, 1):
                val = safe_float(item.value)
                if val is None:
                    continue
                session.add(FinancialLineItem(
                    statement_id=stmt.id,
                    item_key=item.item_key,
                    item_label=item.item_label,
                    value=val,
                    ordinal=ordinal,
                    note=item.note,
                ))
                fin_item_count += 1
    counts["financial_line_items"] = fin_item_count

    # ── Operational Issues ──────────────────────────────────────────────
    for item in data.operational_issues:
        session.add(OperationalIssue(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            topic=item.topic,
            performance=item.performance,
            attribution=item.attribution,
            risk=item.risk,
            guidance=item.guidance,
        ))
    counts["operational_issues"] = len(data.operational_issues)

    # ── Narratives ──────────────────────────────────────────────────────
    for item in data.narratives:
        session.add(CompanyNarrative(
            company_id=company_id,
            raw_post_id=raw_post.id,
            narrative=item.narrative,
            capital_required=safe_float(item.capital_required),
            capital_unit=item.capital_unit,
            promised_outcome=item.promised_outcome,
            deadline=_parse_date(item.deadline),
            reported_at=raw_post.posted_at.date() if raw_post.posted_at else None,
        ))
    counts["narratives"] = len(data.narratives)

    # ── Downstream Segments ─────────────────────────────────────────────
    for item in data.downstream_segments:
        session.add(DownstreamSegment(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            segment=item.segment,
            customer_name=item.customer_name,
            customer_type=item.customer_type,
            products=item.products,
            channels=item.channels,
            revenue=safe_float(item.revenue),
            revenue_pct=safe_float(item.revenue_pct),
            growth_yoy=safe_str(item.growth_yoy),
            backlog=safe_float(item.backlog),
            backlog_note=item.backlog_note,
            pricing_model=item.pricing_model,
            contract_duration=item.contract_duration,
            revenue_type=item.revenue_type,
            is_recurring=item.is_recurring,
            recognition_method=item.recognition_method,
            contract_duration_months=item.contract_duration_months,
            switching_cost_level=item.switching_cost_level,
            description=item.description,
        ))
    counts["downstream_segments"] = len(data.downstream_segments)

    # ── Upstream Segments ───────────────────────────────────────────────
    for item in data.upstream_segments:
        session.add(UpstreamSegment(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            segment=item.segment,
            supplier_name=item.supplier_name,
            supply_type=item.supply_type,
            material_or_service=item.material_or_service,
            process_node=item.process_node,
            geographic_location=item.geographic_location,
            is_sole_source=item.is_sole_source,
            purchase_obligation=safe_float(item.purchase_obligation),
            lead_time=item.lead_time,
            contract_type=item.contract_type,
            prepaid_amount=safe_float(item.prepaid_amount),
            concentration_risk=item.concentration_risk,
            description=item.description,
        ))
    counts["upstream_segments"] = len(data.upstream_segments)

    # ── Geographic Revenues ─────────────────────────────────────────────
    for item in data.geographic_revenues:
        session.add(GeographicRevenue(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            region=item.region,
            revenue=safe_float(item.revenue),
            revenue_share=safe_float(item.revenue_share),
            growth_yoy=safe_str(item.growth_yoy),
            note=item.note,
        ))
    counts["geographic_revenues"] = len(data.geographic_revenues)

    # ── Non-Financial KPIs ──────────────────────────────────────────────
    for item in data.non_financial_kpis:
        session.add(NonFinancialKPI(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            kpi_name=item.kpi_name,
            kpi_value=str(item.kpi_value),
            kpi_unit=item.kpi_unit,
            yoy_change=item.yoy_change,
            category=item.category,
            note=item.note,
        ))
    counts["non_financial_kpis"] = len(data.non_financial_kpis)

    # ── Debt Obligations ────────────────────────────────────────────────
    for item in data.debt_obligations:
        session.add(DebtObligation(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            instrument_name=item.instrument_name,
            debt_type=item.debt_type,
            principal=safe_float(item.principal),
            currency=item.currency,
            interest_rate=safe_float(item.interest_rate),
            maturity_date=_parse_date(item.maturity_date),
            is_secured=item.is_secured,
            is_current=item.is_current,
            is_floating_rate=item.is_floating_rate,
            note=item.note,
        ))
    counts["debt_obligations"] = len(data.debt_obligations)

    # ── Litigations ─────────────────────────────────────────────────────
    for item in data.litigations:
        session.add(Litigation(
            company_id=company_id,
            raw_post_id=raw_post.id,
            case_name=item.case_name,
            case_type=item.case_type,
            status=item.status,
            counterparty=item.counterparty,
            filed_at=_parse_date(item.filed_at),
            claimed_amount=safe_float(item.claimed_amount),
            accrued_amount=safe_float(item.accrued_amount),
            currency=item.currency,
            description=item.description,
        ))
    counts["litigations"] = len(data.litigations)

    # ── Executive Compensations ─────────────────────────────────────────
    for item in data.executive_compensations:
        session.add(ExecutiveCompensation(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            role_type=item.role_type,
            name=item.name,
            title=item.title,
            base_salary=safe_float(item.base_salary),
            bonus=safe_float(item.bonus),
            stock_awards=safe_float(item.stock_awards),
            option_awards=safe_float(item.option_awards),
            non_equity_incentive=safe_float(item.non_equity_incentive),
            other_comp=safe_float(item.other_comp),
            total_comp=safe_float(item.total_comp),
            pay_ratio=safe_float(item.pay_ratio),
            median_employee_comp=safe_float(item.median_employee_comp),
        ))
    counts["executive_compensations"] = len(data.executive_compensations)

    # ── Stock Ownership ─────────────────────────────────────────────────
    for item in data.stock_ownership:
        session.add(StockOwnership(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            name=item.name,
            title=item.title,
            shares_beneficially_owned=item.shares_beneficially_owned,
            percent_of_class=safe_float(item.percent_of_class),
        ))
    counts["stock_ownership"] = len(data.stock_ownership)

    # ── Related Party Transactions ──────────────────────────────────────
    for item in data.related_party_transactions:
        session.add(RelatedPartyTransaction(
            company_id=company_id,
            period=period,
            raw_post_id=raw_post.id,
            related_party=item.related_party,
            relationship=item.relationship,
            transaction_type=item.transaction_type,
            amount=safe_float(item.amount),
            currency=item.currency,
            terms=item.terms,
            is_ongoing=item.is_ongoing,
            description=item.description,
        ))
    counts["related_party_transactions"] = len(data.related_party_transactions)

    # ── Pricing Actions ──────────────────────────────────────────────
    for item in data.pricing_actions:
        session.add(PricingAction(
            company_id=company_id,
            period=period,
            product_or_segment=item.product_or_segment,
            price_change_pct=safe_float(item.price_change_pct),
            volume_impact_pct=safe_float(item.volume_impact_pct),
            effective_date=_parse_date(item.effective_date),
            raw_post_id=raw_post.id,
        ))
    counts["pricing_actions"] = len(data.pricing_actions)

    # ── Competitor Relations ─────────────────────────────────────────
    for item in data.competitor_relations:
        session.add(CompetitorRelation(
            company_id=company_id,
            competitor_name=item.competitor_name,
            market_segment=item.market_segment,
            relationship_type=item.relationship_type,
            raw_post_id=raw_post.id,
        ))
    counts["competitor_relations"] = len(data.competitor_relations)

    # ── Market Share Data ────────────────────────────────────────────
    for item in data.market_share_data:
        session.add(MarketShareData(
            company_id=company_id,
            market_segment=item.market_segment,
            period=period,
            share_pct=safe_float(item.share_pct),
            source_description=item.source_description,
            raw_post_id=raw_post.id,
        ))
    counts["market_share_data"] = len(data.market_share_data)

    # ── Known Issues ─────────────────────────────────────────────────
    for item in data.known_issues:
        session.add(KnownIssue(
            company_id=company_id,
            period=period,
            issue_description=item.issue_description,
            issue_category=item.issue_category,
            severity=item.severity,
            source_type=item.source_type,
            raw_post_id=raw_post.id,
        ))
    counts["known_issues"] = len(data.known_issues)

    # ── Management Acknowledgments ───────────────────────────────────
    for item in data.management_acknowledgments:
        session.add(ManagementAcknowledgment(
            company_id=company_id,
            period=period,
            issue_description=item.issue_description,
            response_quality=item.response_quality,
            has_action_plan=item.has_action_plan,
            raw_post_id=raw_post.id,
        ))
    counts["management_acknowledgments"] = len(data.management_acknowledgments)

    # ── Executive Changes ────────────────────────────────────────────
    for item in data.executive_changes:
        session.add(ExecutiveChange(
            company_id=company_id,
            person_name=item.person_name,
            title=item.title,
            change_type=item.change_type,
            change_date=_parse_date(item.change_date),
            reason=item.reason,
            raw_post_id=raw_post.id,
        ))
    counts["executive_changes"] = len(data.executive_changes)

    # ── Audit Opinion ────────────────────────────────────────────────
    if data.audit_opinion:
        session.add(AuditOpinion(
            company_id=company_id,
            period=period,
            opinion_type=data.audit_opinion.opinion_type,
            auditor_name=data.audit_opinion.auditor_name,
            emphasis_matters=data.audit_opinion.emphasis_matters,
            raw_post_id=raw_post.id,
        ))
        counts["audit_opinions"] = 1
    else:
        counts["audit_opinions"] = 0

    # ── Management Guidance ──────────────────────────────────────────
    for item in data.management_guidance:
        session.add(ManagementGuidance(
            company_id=company_id,
            source_period=period,
            target_period=item.target_period,
            metric=item.metric,
            value_low=safe_float(item.value_low),
            value_high=safe_float(item.value_high),
            unit=item.unit,
            confidence_language=item.confidence_language,
            verbatim=item.verbatim,
            raw_post_id=raw_post.id,
        ))
    counts["management_guidance"] = len(data.management_guidance)

    # ── 更新 RawPost ──────────────────────────────────────────────────
    raw_post.is_processed = True
    raw_post.processed_at = _utcnow()
    if data.summary:
        raw_post.content_summary = data.summary
    session.add(raw_post)
    await session.commit()

    total = sum(counts.values())
    logger.info(f"[Company] Write done: {total} rows across {len([v for v in counts.values() if v])} tables")

    return {
        "is_relevant_content": True,
        "skip_reason": None,
        "table_counts": counts,
        "summary": data.summary,
        "one_liner": data.one_liner,
        "company_name": company.name,
        "company_ticker": company.ticker,
    }


# ── 主入口（串行 compute + write）──────────────────────────────────────


async def extract_company(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
) -> dict | None:
    """Company 域提取入口：LLM 提取 → 22 张表写入 DB。

    串行接口，并发场景请直接使用 compute + write。
    """
    compute_result = await extract_company_compute(content, platform, author, today)
    return await extract_company_write(raw_post, session, compute_result)
