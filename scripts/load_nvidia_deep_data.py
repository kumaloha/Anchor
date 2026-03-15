"""将 NVIDIA 深度提取结果（6主题 × 5财年）写入 DB"""
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
)

DEEP_DIR = Path("/tmp/nvidia_deep")
FY_PERIOD = {
    "fy2021": "FY2021", "fy2022": "FY2022", "fy2023": "FY2023",
    "fy2024": "FY2024", "fy2025": "FY2025",
}


def safe_float(val) -> float | None:
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
    if val is None:
        return None
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else None
    return str(val)


async def get_or_create_company(session) -> int:
    result = await session.exec(
        select(CompanyProfile).where(CompanyProfile.ticker == "NVDA")
    )
    company = result.first()
    if company:
        return company.id
    company = CompanyProfile(
        name="NVIDIA Corporation", ticker="NVDA",
        market="us", industry="Semiconductors",
        summary="GPU and AI computing platform company",
    )
    session.add(company)
    await session.flush()
    return company.id


async def clear_old_data(session, company_id: int):
    """清除旧数据（仅清除会被重写的表）"""
    import sqlalchemy as sa
    tables = [
        OperationalIssue, CompanyNarrative,
        DownstreamSegment, UpstreamSegment,
        GeographicRevenue, NonFinancialKPI,
        DebtObligation, Litigation,
    ]
    for model in tables:
        await session.exec(
            sa.delete(model).where(model.company_id == company_id)  # type: ignore
        )
    await session.flush()


def load_topic(name: str) -> dict:
    path = DEEP_DIR / f"{name}.json"
    return json.loads(path.read_text())


async def load_supply_chain(session, company_id: int, counts: dict):
    data = load_topic("supply_chain")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

        # 客户集中度 → downstream_segments
        for c in d.get("customer_concentration", []):
            session.add(DownstreamSegment(
                company_id=company_id, period=period,
                segment=safe_str(c.get("segment")),
                customer_name=c.get("customer_name", "Unknown"),
                customer_type=c.get("customer_type"),
                products=safe_str(c.get("products")),
                channels=safe_str(c.get("channels")),
                revenue=safe_float(c.get("revenue_amount")),
                revenue_pct=safe_float(c.get("revenue_pct")),
                backlog=safe_float(c.get("backlog")),
                backlog_note=safe_str(c.get("backlog_note")),
                pricing_model=safe_str(c.get("pricing_model")),
                contract_duration=safe_str(c.get("contract_duration")),
                description=safe_str(c.get("note")),
            ))
            counts["downstream"] = counts.get("downstream", 0) + 1

        # 上游供应商 → upstream_segments
        for s in d.get("upstream_details", []):
            session.add(UpstreamSegment(
                company_id=company_id, period=period,
                segment=safe_str(s.get("segment")),
                supplier_name=s.get("supplier_name", "Unknown"),
                supply_type=s.get("supply_type", "other"),
                material_or_service=safe_str(s.get("material_or_service")),
                process_node=safe_str(s.get("process_node")),
                geographic_location=safe_str(s.get("geographic_location")),
                is_sole_source=bool(s.get("is_sole_source")),
                purchase_obligation=safe_float(s.get("purchase_obligation")),
                lead_time=safe_str(s.get("lead_time")),
                contract_type=safe_str(s.get("contract_type")),
                prepaid_amount=safe_float(s.get("prepaid_amount")),
                concentration_risk=safe_str(s.get("concentration_risk")),
                description=safe_str(s.get("note")),
            ))
            counts["upstream"] = counts.get("upstream", 0) + 1


async def load_operations(session, company_id: int, counts: dict):
    data = load_topic("operations")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

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

        for item in d.get("narratives", []):
            session.add(CompanyNarrative(
                company_id=company_id,
                narrative=item.get("narrative", ""),
                capital_required=safe_float(item.get("capital_required")),
                capital_unit=item.get("capital_unit"),
                promised_outcome=item.get("promised_outcome"),
            ))
            counts["narratives"] = counts.get("narratives", 0) + 1


async def load_financials(session, company_id: int, counts: dict):
    data = load_topic("financials")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

        for item in d.get("debt_obligations", []):
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


async def load_risk_kpi(session, company_id: int, counts: dict):
    data = load_topic("risk_kpi")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

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


async def load_revenue_model(session, company_id: int, counts: dict):
    data = load_topic("revenue_model")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

        for s in d.get("revenue_streams", []):
            session.add(DownstreamSegment(
                company_id=company_id, period=period,
                segment=safe_str(s.get("segment")),
                customer_name=s.get("stream_name", "Unknown"),
                revenue=safe_float(s.get("revenue")),
                revenue_pct=safe_float(s.get("revenue_share")),
                growth_yoy=safe_str(s.get("growth_yoy")),
                pricing_model=safe_str(s.get("pricing_model")),
                contract_duration=safe_str(s.get("contract_duration")),
                revenue_type=safe_str(s.get("revenue_type")),
                is_recurring=s.get("is_recurring"),
                recognition_method=safe_str(s.get("recognition_method")),
                description=safe_str(s.get("description")),
            ))
            counts["downstream_revenue"] = counts.get("downstream_revenue", 0) + 1


async def load_geographic(session, company_id: int, counts: dict):
    data = load_topic("geographic")
    for fy, d in data.items():
        period = FY_PERIOD[fy]

        for item in d.get("geographic_revenues", []):
            session.add(GeographicRevenue(
                company_id=company_id, period=period,
                region=item.get("region", ""),
                revenue=safe_float(item.get("revenue")),
                revenue_share=safe_float(item.get("revenue_share")),
                growth_yoy=safe_str(item.get("growth_yoy")),
                note=safe_str(item.get("note")),
            ))
            counts["geographic_revenues"] = counts.get("geographic_revenues", 0) + 1


async def main():
    async with AsyncSessionLocal() as session:
        company_id = await get_or_create_company(session)
        print(f"Company ID: {company_id}")

        print("清除旧数据...")
        await clear_old_data(session, company_id)

        counts: dict[str, int] = {}

        print("加载 supply_chain...")
        await load_supply_chain(session, company_id, counts)

        print("加载 operations...")
        await load_operations(session, company_id, counts)

        print("加载 financials...")
        await load_financials(session, company_id, counts)

        print("加载 risk_kpi...")
        await load_risk_kpi(session, company_id, counts)

        print("加载 revenue_model...")
        await load_revenue_model(session, company_id, counts)

        print("加载 geographic...")
        await load_geographic(session, company_id, counts)

        await session.commit()

        print("\n写入完成:")
        for k, v in sorted(counts.items()):
            print(f"  {k}: {v} rows")


if __name__ == "__main__":
    asyncio.run(main())
