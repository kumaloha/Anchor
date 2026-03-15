"""
Company 域 Pydantic Schemas — LLM 输出校验
==========================================
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── LLM 输出子模型 ─────────────────────────────────────────────────────


class ExtractedOperationalIssue(BaseModel):
    topic: str
    performance: Optional[str] = None
    attribution: Optional[str] = None
    risk: Optional[str] = None
    guidance: Optional[str] = None


class ExtractedNarrative(BaseModel):
    narrative: str
    capital_required: Optional[float] = None
    capital_unit: Optional[str] = None
    promised_outcome: Optional[str] = None
    deadline: Optional[str] = None  # YYYY-MM-DD or null


class ExtractedDownstream(BaseModel):
    segment: Optional[str] = None
    customer_name: str
    customer_type: Optional[str] = None
    products: Optional[str] = None
    channels: Optional[str] = None
    revenue: Optional[float] = None
    revenue_pct: Optional[float] = None
    growth_yoy: Optional[str] = None
    backlog: Optional[float] = None
    backlog_note: Optional[str] = None
    pricing_model: Optional[str] = None
    contract_duration: Optional[str] = None
    revenue_type: Optional[str] = None
    is_recurring: Optional[bool] = None
    recognition_method: Optional[str] = None
    description: Optional[str] = None


class ExtractedUpstream(BaseModel):
    segment: Optional[str] = None
    supplier_name: str
    supply_type: str
    material_or_service: Optional[str] = None
    process_node: Optional[str] = None
    geographic_location: Optional[str] = None
    is_sole_source: bool = False
    purchase_obligation: Optional[float] = None
    lead_time: Optional[str] = None
    contract_type: Optional[str] = None
    prepaid_amount: Optional[float] = None
    concentration_risk: Optional[str] = None
    description: Optional[str] = None


class ExtractedGeographicRevenue(BaseModel):
    region: str
    revenue: Optional[float] = None
    revenue_share: Optional[float] = None
    growth_yoy: Optional[str] = None
    note: Optional[str] = None


class ExtractedNonFinancialKPI(BaseModel):
    kpi_name: str
    kpi_value: str
    kpi_unit: Optional[str] = None
    yoy_change: Optional[str] = None
    category: Optional[str] = None
    note: Optional[str] = None


class ExtractedDebtObligation(BaseModel):
    instrument_name: str
    debt_type: str = "bond"
    principal: Optional[float] = None
    currency: str = "USD"
    interest_rate: Optional[float] = None
    maturity_date: Optional[str] = None
    is_secured: bool = False
    is_current: bool = False
    note: Optional[str] = None


class ExtractedLitigation(BaseModel):
    case_name: str
    case_type: str = "other"
    status: str = "pending"
    counterparty: Optional[str] = None
    filed_at: Optional[str] = None
    claimed_amount: Optional[float] = None
    accrued_amount: Optional[float] = None
    currency: str = "USD"
    description: Optional[str] = None


class ExtractedExecutiveCompensation(BaseModel):
    name: str
    title: str = ""
    role_type: str = "executive"
    base_salary: Optional[float] = None
    bonus: Optional[float] = None
    stock_awards: Optional[float] = None
    option_awards: Optional[float] = None
    non_equity_incentive: Optional[float] = None
    other_comp: Optional[float] = None
    total_comp: Optional[float] = None
    pay_ratio: Optional[float] = None
    median_employee_comp: Optional[float] = None


class ExtractedStockOwnership(BaseModel):
    name: str
    title: Optional[str] = None
    shares_beneficially_owned: Optional[int] = None
    percent_of_class: Optional[float] = None


class ExtractedRelatedPartyTransaction(BaseModel):
    related_party: str
    relationship: str = "other"
    transaction_type: str = "other"
    amount: Optional[float] = None
    currency: str = "USD"
    terms: Optional[str] = None
    is_ongoing: bool = False
    description: Optional[str] = None


# ── 顶层 LLM 输出模型 ──────────────────────────────────────────────────


class CompanyProfile(BaseModel):
    """公司基本信息（LLM 输出）"""
    name: str
    ticker: str
    market: str = "us"
    industry: Optional[str] = None
    summary: Optional[str] = None


class CompanyExtractionResult(BaseModel):
    """Company 域 LLM 提取结果（全量）"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None

    # 公司识别
    company: Optional[CompanyProfile] = None
    period: str = ""  # "FY2025" / "2025Q4"

    # 13 张表的数据
    operational_issues: list[ExtractedOperationalIssue] = []
    narratives: list[ExtractedNarrative] = []
    downstream_segments: list[ExtractedDownstream] = []
    upstream_segments: list[ExtractedUpstream] = []
    geographic_revenues: list[ExtractedGeographicRevenue] = []
    non_financial_kpis: list[ExtractedNonFinancialKPI] = []
    debt_obligations: list[ExtractedDebtObligation] = []
    litigations: list[ExtractedLitigation] = []
    executive_compensations: list[ExtractedExecutiveCompensation] = []
    stock_ownership: list[ExtractedStockOwnership] = []
    related_party_transactions: list[ExtractedRelatedPartyTransaction] = []

    # 摘要
    summary: Optional[str] = None
    one_liner: Optional[str] = None
