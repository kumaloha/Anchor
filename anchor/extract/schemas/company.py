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
    contract_duration_months: Optional[int] = None
    switching_cost_level: Optional[str] = None
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
    is_floating_rate: bool = False
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


# ── Axion 新增表 schemas ──────────────────────────────────────────────


class ExtractedPricingAction(BaseModel):
    product_or_segment: str
    price_change_pct: Optional[float] = None
    volume_impact_pct: Optional[float] = None
    effective_date: Optional[str] = None


class ExtractedCompetitorRelation(BaseModel):
    competitor_name: str
    market_segment: Optional[str] = None
    relationship_type: str = "direct_competitor"


class ExtractedMarketShareData(BaseModel):
    company_or_competitor: str
    market_segment: str
    share_pct: Optional[float] = None
    source_description: Optional[str] = None


class ExtractedKnownIssue(BaseModel):
    issue_description: str
    issue_category: str = "operational"
    severity: str = "major"
    source_type: str = "news"


class ExtractedManagementAcknowledgment(BaseModel):
    issue_description: str
    response_quality: str = "forthright"
    has_action_plan: bool = False


class ExtractedExecutiveChange(BaseModel):
    person_name: str
    title: Optional[str] = None
    change_type: str = "joined"
    change_date: Optional[str] = None
    reason: Optional[str] = None


class ExtractedAuditOpinion(BaseModel):
    opinion_type: str = "unqualified"
    auditor_name: Optional[str] = None
    emphasis_matters: Optional[str] = None


class ExtractedManagementGuidance(BaseModel):
    target_period: Optional[str] = None
    metric: str
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    unit: str = "absolute"
    confidence_language: Optional[str] = None
    verbatim: Optional[str] = None


class ExtractedFinancialLineItem(BaseModel):
    item_key: str
    item_label: str
    value: float
    note: Optional[str] = None


class ExtractedFinancialStatements(BaseModel):
    """三表财务数据（利润表、资产负债表、现金流量表）"""
    currency: str = "USD"
    income: list[ExtractedFinancialLineItem] = []
    balance_sheet: list[ExtractedFinancialLineItem] = []
    cashflow: list[ExtractedFinancialLineItem] = []


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

    # 财务三表
    financial_statements: Optional[ExtractedFinancialStatements] = None

    # Axion 新增表
    pricing_actions: list[ExtractedPricingAction] = []
    competitor_relations: list[ExtractedCompetitorRelation] = []
    market_share_data: list[ExtractedMarketShareData] = []
    known_issues: list[ExtractedKnownIssue] = []
    management_acknowledgments: list[ExtractedManagementAcknowledgment] = []
    executive_changes: list[ExtractedExecutiveChange] = []
    audit_opinion: Optional[ExtractedAuditOpinion] = None
    management_guidance: list[ExtractedManagementGuidance] = []

    # 业务表
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
