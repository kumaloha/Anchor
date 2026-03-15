"""将 NVIDIA 多年提取结果（10-K + Proxy）写入 DB"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from sqlmodel import select
from anchor.database.session import AsyncSessionLocal
from anchor.models import (
    CompanyProfile,
    CompanyNarrative,
    OperationalIssue,
    DownstreamSegment,
    UpstreamSegment,
    GeographicRevenue,
    NonFinancialKPI,
    DebtObligation,
    Litigation,
    ExecutiveCompensation,
    StockOwnership,
    RelatedPartyTransaction,
)

def safe_float(val) -> float | None:
    """Clean and convert to float, handling '0.309%' etc."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().rstrip("%")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def safe_str(val) -> str | None:
    """Convert any value to string, handling lists etc."""
    if val is None:
        return None
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else None
    return str(val)


FY_PERIOD = {
    "fy2021": "FY2021",
    "fy2022": "FY2022",
    "fy2023": "FY2023",
    "fy2024": "FY2024",
    "fy2025": "FY2025",
}


async def get_or_create_company(session) -> int:
    result = await session.exec(
        select(CompanyProfile).where(CompanyProfile.ticker == "NVDA")
    )
    company = result.first()
    if company:
        return company.id
    company = CompanyProfile(
        name="NVIDIA Corporation",
        ticker="NVDA",
        market="us",
        industry="Semiconductors",
        summary="GPU and AI computing platform company",
    )
    session.add(company)
    await session.flush()
    return company.id


async def load_10k_data(session, company_id: int):
    data = json.loads(Path("/tmp/nvidia_multiyear_extract.json").read_text())
    counts = {}

    for fy, d in data.items():
        period = FY_PERIOD[fy]

        # Operational Issues
        for item in d.get("operational_issues", []):
            session.add(OperationalIssue(
                company_id=company_id, period=period,
                topic=item.get("topic", ""),
                performance=item.get("performance"),
                attribution=item.get("attribution"),
                risk=item.get("risk"),
                guidance=item.get("guidance"),
            ))
            counts["operational_issues"] = counts.get("operational_issues", 0) + 1

        # Narratives
        for item in d.get("narratives", []):
            session.add(CompanyNarrative(
                company_id=company_id,
                narrative=item.get("narrative", ""),
                capital_required=item.get("capital_required"),
                capital_unit=item.get("capital_unit"),
                promised_outcome=item.get("promised_outcome"),
            ))
            counts["narratives"] = counts.get("narratives", 0) + 1

        # Downstream Segments
        for item in d.get("downstream_segments", []):
            session.add(DownstreamSegment(
                company_id=company_id, period=period,
                segment_name=item.get("segment_name", ""),
                product_or_service=safe_str(item.get("product_or_service")),
                customer_type=safe_str(item.get("customer_type")),
                key_customers=safe_str(item.get("key_customers")),
                revenue=safe_float(item.get("revenue")),
                revenue_share=safe_float(item.get("revenue_share")),
                growth_yoy=safe_str(item.get("growth_yoy")),
                description=safe_str(item.get("description")),
            ))
            counts["downstream_segments"] = counts.get("downstream_segments", 0) + 1

        # Upstream Segments
        for item in d.get("upstream_segments", []):
            session.add(UpstreamSegment(
                company_id=company_id, period=period,
                supplier_name=safe_str(item.get("supplier_name")) or "",
                supply_type=safe_str(item.get("supply_type")) or "",
                material_or_service=safe_str(item.get("material_or_service")),
                concentration_risk=safe_str(item.get("concentration_risk")),
                cost_share=safe_float(item.get("cost_share")),
                description=safe_str(item.get("description")),
            ))
            counts["upstream_segments"] = counts.get("upstream_segments", 0) + 1

        # Geographic Revenues
        for item in d.get("geographic_revenues", []):
            session.add(GeographicRevenue(
                company_id=company_id, period=period,
                region=item.get("region", ""),
                revenue=safe_float(item.get("revenue")),
                revenue_share=safe_float(item.get("revenue_share")),
                growth_yoy=safe_str(item.get("growth_yoy")),
            ))
            counts["geographic_revenues"] = counts.get("geographic_revenues", 0) + 1

        # Non-Financial KPIs
        for item in d.get("non_financial_kpis", []):
            session.add(NonFinancialKPI(
                company_id=company_id, period=period,
                kpi_name=item.get("kpi_name", ""),
                kpi_value=str(item.get("kpi_value", "")),
                kpi_unit=item.get("kpi_unit"),
                yoy_change=item.get("yoy_change"),
                category=item.get("category"),
            ))
            counts["non_financial_kpis"] = counts.get("non_financial_kpis", 0) + 1

        # Debt Obligations
        for item in d.get("debt_obligations", []):
            # parse maturity_date: "2026" or "2026-01-01" → date or None
            mat = item.get("maturity_date")
            mat_date = None
            if mat:
                mat_str = str(mat).strip()
                try:
                    if len(mat_str) == 4:
                        mat_date = date(int(mat_str), 1, 1)
                    else:
                        mat_date = date.fromisoformat(mat_str[:10])
                except (ValueError, TypeError):
                    pass
            session.add(DebtObligation(
                company_id=company_id, period=period,
                instrument_name=item.get("instrument_name", ""),
                debt_type=item.get("debt_type", "bond"),
                principal=safe_float(item.get("principal")),
                interest_rate=safe_float(item.get("interest_rate")),
                maturity_date=mat_date,
            ))
            counts["debt_obligations"] = counts.get("debt_obligations", 0) + 1

        # Litigations
        for item in d.get("litigations", []):
            session.add(Litigation(
                company_id=company_id,
                case_name=item.get("case_name", ""),
                case_type=item.get("case_type", "other"),
                status=item.get("status", "pending"),
                counterparty=item.get("counterparty"),
                description=item.get("description"),
            ))
            counts["litigations"] = counts.get("litigations", 0) + 1

        # Equity / Tax / SBC → financial_statements + line_items
        # (already loaded via load_nvidia_financials.py, skip here)

    return counts


async def load_proxy_data(session, company_id: int):
    data = json.loads(Path("/tmp/nvidia_proxy_multiyear_extract.json").read_text())
    counts = {}

    for fy, d in data.items():
        period = FY_PERIOD[fy]

        # Executive Compensations
        for item in d.get("executive_compensations", []):
            fiscal_year = item.get("fiscal_year", period)
            # normalize fiscal_year to our format
            if isinstance(fiscal_year, str):
                fiscal_year = fiscal_year.upper().replace(" ", "")
                if not fiscal_year.startswith("FY"):
                    fiscal_year = period

            ceo_pay = d.get("ceo_pay_ratio", {})
            is_ceo = "ceo" in item.get("title", "").lower() or "huang" in item.get("name", "").lower()

            session.add(ExecutiveCompensation(
                company_id=company_id,
                period=fiscal_year,
                role_type="executive",
                name=item.get("name", ""),
                title=item.get("title", ""),
                base_salary=item.get("base_salary"),
                bonus=item.get("bonus"),
                stock_awards=item.get("stock_awards"),
                option_awards=item.get("option_awards"),
                non_equity_incentive=item.get("non_equity_incentive"),
                other_comp=item.get("other_comp"),
                total_comp=item.get("total_comp"),
                pay_ratio=ceo_pay.get("ratio") if is_ceo else None,
                median_employee_comp=ceo_pay.get("median_employee_total") if is_ceo else None,
            ))
            counts["exec_comp"] = counts.get("exec_comp", 0) + 1

        # Director Compensations → same table, role_type="director"
        for item in d.get("director_compensations", []):
            session.add(ExecutiveCompensation(
                company_id=company_id,
                period=period,
                role_type="director",
                name=item.get("name", ""),
                title="Independent Director",
                base_salary=item.get("fees_earned_cash"),
                stock_awards=item.get("stock_awards"),
                option_awards=item.get("option_awards"),
                other_comp=item.get("all_other"),
                total_comp=item.get("total"),
            ))
            counts["director_comp"] = counts.get("director_comp", 0) + 1

        # Stock Ownership
        for item in d.get("stock_ownership", []):
            session.add(StockOwnership(
                company_id=company_id,
                period=period,
                name=item.get("name", ""),
                title=item.get("title"),
                shares_beneficially_owned=item.get("shares_beneficially_owned"),
                percent_of_class=item.get("percent_of_class"),
            ))
            counts["stock_ownership"] = counts.get("stock_ownership", 0) + 1

        # Related Party Transactions
        for item in d.get("related_party_transactions", []):
            session.add(RelatedPartyTransaction(
                company_id=company_id,
                period=period,
                related_party=item.get("related_party", ""),
                relationship=item.get("relationship", "other"),
                transaction_type=item.get("transaction_type", "other"),
                amount=item.get("amount"),
                description=item.get("description"),
            ))
            counts["related_party"] = counts.get("related_party", 0) + 1

    return counts


async def main():
    async with AsyncSessionLocal() as session:
        company_id = await get_or_create_company(session)
        print(f"Company ID: {company_id}")

        print("\n加载 10-K 数据...")
        c1 = await load_10k_data(session, company_id)
        for k, v in sorted(c1.items()):
            print(f"  {k}: {v} rows")

        print("\n加载 Proxy 数据...")
        c2 = await load_proxy_data(session, company_id)
        for k, v in sorted(c2.items()):
            print(f"  {k}: {v} rows")

        await session.commit()
        print("\n全部写入完成 ✓")


if __name__ == "__main__":
    asyncio.run(main())
