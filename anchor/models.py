"""
Anchor 核心数据模型
==================
政策模型（结构化政策文件）：
  PolicyDocument / PolicyDirective / PolicyLink

公司模型（巴菲特标准）：
  CompanyProfile / CompanyNarrative / OperationalIssue
  FinancialStatement / FinancialLineItem
  DownstreamSegment / UpstreamSegment / GeographicRevenue
  NonFinancialKPI / DebtObligation / Litigation
  ExecutiveCompensation / StockOwnership / RelatedPartyTransaction

技术模型：
  TechInsight / PatentRight / PatentCommercial

基础设施表：
  AuthorGroup / Topic / Author / MonitoredSource / RawPost
  PostQualityAssessment / AuthorStanceProfile / AuthorStats
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)



# ===========================================================================
# 枚举
# ===========================================================================


class SourceType(str, Enum):
    POST = "post"
    PROFILE = "profile"


# ===========================================================================
# 基础设施表（保留，与 v2.2 兼容）
# ===========================================================================


class AuthorGroup(SQLModel, table=True):
    """跨平台作者实体 — 将不同平台的同一真实人物关联起来"""

    __tablename__ = "author_groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_name: str
    canonical_role: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Topic(SQLModel, table=True):
    """话题"""

    __tablename__ = "topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Author(SQLModel, table=True):
    """观点作者"""

    __tablename__ = "authors"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    platform: str
    platform_id: Optional[str] = None
    profile_url: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    # AuthorProfiler 填写
    role: Optional[str] = None
    expertise_areas: Optional[str] = None
    known_biases: Optional[str] = None
    credibility_tier: Optional[int] = None
    profile_note: Optional[str] = None
    # 当前处境：最新民调、选举压力、政治/市场处境等（≤150字）
    situation_note: Optional[str] = None
    profile_fetched: bool = False
    profile_fetched_at: Optional[datetime] = None

    author_group_id: Optional[int] = Field(
        default=None, foreign_key="author_groups.id", index=True
    )


class MonitoredSource(SQLModel, table=True):
    """监控源"""

    __tablename__ = "monitored_sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    source_type: SourceType
    platform: str
    platform_id: str

    author_id: Optional[int] = Field(default=None, foreign_key="authors.id")

    is_active: bool = True
    fetch_interval_minutes: int = 60
    last_fetched_at: Optional[datetime] = None
    history_fetched: bool = False

    created_at: datetime = Field(default_factory=_utcnow)


class RawPost(SQLModel, table=True):
    """原始帖子 — 采集的未处理内容"""

    __tablename__ = "raw_posts"

    id: Optional[int] = Field(default=None, primary_key=True)

    source: str
    external_id: str = Field(index=True)
    content: str
    enriched_content: Optional[str] = None

    context_fetched: bool = False
    has_context: bool = False

    author_name: str
    author_platform_id: Optional[str] = None
    url: str
    posted_at: datetime
    collected_at: datetime = Field(default_factory=_utcnow)
    raw_metadata: Optional[str] = None

    media_json: Optional[str] = None

    is_processed: bool = False
    processed_at: Optional[datetime] = None
    content_summary: Optional[str] = None       # 内容提取 Step5 叙事摘要

    # 政策文档专属字段（内容提取 policy 模式写入）
    issuing_authority: Optional[str] = None     # 发文机关（如"国务院"）
    authority_level: Optional[str] = None       # 顶层设计|部委联合|部委独立

    # 通用判断 — 2D 分类 + 利益冲突 + 摘要
    notion_page_id: Optional[str] = None        # Notion 页面 ID（同步后写回）
    content_type: Optional[str] = None          # 过渡兼容：财经分析|市场动向|产业链研究|公司调研|技术论文|公司财报|政策解读
    content_type_secondary: Optional[str] = None  # 次分类（可选）
    content_subtype: Optional[str] = None       # 财经分析子分类（旧，不再写入）
    content_topic: Optional[str] = None         # 具体主题（≤30字）
    author_intent: Optional[str] = None         # 旧字段，现映射为 assessment_summary
    intent_note: Optional[str] = None           # 意图说明（旧，不再写入）
    policy_delta: Optional[str] = None          # 政策对比：与上一年同类政策的核心变化（≤150字）
    content_domain: Optional[str] = None        # 政策|产业|公司|期货|技术
    content_nature: Optional[str] = None        # 一手信息|第三方分析
    has_conflict: Optional[bool] = None         # 与读者是否利益冲突
    conflict_note: Optional[str] = None         # 冲突风险描述 ≤80字
    assessment_summary: Optional[str] = None    # 什么人在干什么事 ≤80字

    assessed: bool = Field(default=False, sa_column_kwargs={"name": "assessed"})
    assessed_at: Optional[datetime] = None

    is_duplicate: bool = False
    original_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id"
    )

    monitored_source_id: Optional[int] = Field(
        default=None, foreign_key="monitored_sources.id"
    )



# ===========================================================================
# 评估与统计表（保留，通用判断/事实验证使用）
# ===========================================================================


class PostQualityAssessment(SQLModel, table=True):
    """单篇内容质量评估"""

    __tablename__ = "post_quality_assessments"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", unique=True, index=True)
    author_id: int = Field(foreign_key="authors.id", index=True)

    uniqueness_score: Optional[float] = None
    uniqueness_note: Optional[str] = None
    is_first_mover: Optional[bool] = None
    similar_claim_count: int = 0
    similar_author_count: int = 0

    effectiveness_score: Optional[float] = None
    effectiveness_note: Optional[str] = None
    noise_ratio: Optional[float] = None
    noise_types: Optional[str] = None           # JSON array

    # 文章立场分析
    stance_label: Optional[str] = None
    stance_note: Optional[str] = None

    assessed_at: datetime = Field(default_factory=_utcnow)


class AuthorStanceProfile(SQLModel, table=True):
    """作者立场分布档案（已停止写入，表保留兼容）"""

    __tablename__ = "author_stance_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int = Field(foreign_key="authors.id", unique=True, index=True)

    # JSON dict: {"看涨/多头": 5, "看跌/空头": 2, ...}
    stance_distribution: Optional[str] = None
    dominant_stance: Optional[str] = None
    dominant_stance_ratio: Optional[float] = None
    total_analyzed: int = 0

    # 旧版通用判断 LLM 分析结果（已停止写入）
    audience: Optional[str] = None               # 目标受众（≤40字）
    core_message: Optional[str] = None           # 核心信息（≤80字）
    author_summary: Optional[str] = None         # 综合描述（≤100字）

    last_updated: datetime = Field(default_factory=_utcnow)


class AuthorStats(SQLModel, table=True):
    """作者综合评估统计"""

    __tablename__ = "author_stats"

    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int = Field(foreign_key="authors.id", unique=True, index=True)

    fact_accuracy_rate: Optional[float] = None
    fact_accuracy_sample: int = 0

    conclusion_accuracy_rate: Optional[float] = None
    conclusion_accuracy_sample: int = 0

    prediction_accuracy_rate: Optional[float] = None
    prediction_accuracy_sample: int = 0

    overall_credibility_score: Optional[float] = None

    total_posts_analyzed: int = 0
    last_updated: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 政策模型（独立于 Node/Edge，结构化政策文件）
# ===========================================================================


class PolicyDocument(SQLModel, table=True):
    """政策文件 — 一份文件一条记录"""

    __tablename__ = "policy_documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)                # "十四五规划和2035年远景目标纲要"
    doc_type: str                                  # strategy|plan|annual|budget|monetary|review
    issuing_body: str                              # "全国人大" / "国务院" / "财政部" / "央行"
    authority_level: str                           # top|ministry_joint|ministry|local
    period_start: Optional[date] = None            # 覆盖期起
    period_end: Optional[date] = None              # 覆盖期止
    published_at: date                             # 发布日期
    source_url: Optional[str] = None               # 原文链接
    parent_doc_id: Optional[int] = Field(
        default=None, foreign_key="policy_documents.id", index=True
    )                                              # 上位文件（专项规划→纲要）
    created_at: datetime = Field(default_factory=_utcnow)


class PolicyDirective(SQLModel, table=True):
    """政策指令 — 树状结构 + 原子级政策条目

    level=part/chapter/section 构成文件目录树，
    level=directive 是可独立追踪的原子级政策条目。
    """

    __tablename__ = "policy_directives"

    id: Optional[int] = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="policy_documents.id", index=True)

    # ── 树状结构 ──
    parent_id: Optional[int] = Field(
        default=None, foreign_key="policy_directives.id", index=True
    )
    level: str                                     # part|chapter|section|directive
    ordinal: int = 0                               # 同级排序

    # ── 内容 ──
    title: str                                     # 所有 level 都有标题
    content: Optional[str] = None                  # directive 级别的原文（≤500字）
    summary: Optional[str] = None                  # ≤50字摘要（LLM 生成）

    # ── 分类（仅 directive 级别） ──
    directive_type: Optional[str] = None           # goal|instrument|resource|constraint|accountability
    force_direction: Optional[str] = None          # promote|restrict|regulate|neutral
    specificity: Optional[str] = None              # directional|targeted|quantified
    target_value: Optional[str] = None             # "5%" / "100亿" / "1200万人"

    # ── 作用点（下游查询核心） ──
    sectors_json: Optional[str] = None             # JSON: ["芯片","AI","量子计算"]
    entities_json: Optional[str] = None            # JSON: ["华为","中芯国际"]（如有）

    # ── 动因分析 ──
    rationale: Optional[str] = None                # 为什么提这条？≤200字（LLM 推断）
    rationale_type: Optional[str] = None           # problem 问题驱动 | continuation 路径延续 | shock 外部冲击 | resource 资源条件 | constraint 内部矛盾/约束

    # ── 执行追踪（保留，低优先级） ──
    status: str = "planned"                        # planned|active|completed|superseded|abandoned
    deadline: Optional[date] = None
    actual_value: Optional[str] = None
    progress_note: Optional[str] = None

    # ── 溯源 ──
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class PolicyLink(SQLModel, table=True):
    """政策关联 — 指令之间的关系"""

    __tablename__ = "policy_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="policy_directives.id", index=True)
    target_id: int = Field(foreign_key="policy_directives.id", index=True)
    link_type: str                                 # implements|funds|constrains|supersedes|enables|conflicts|measures
    note: Optional[str] = None                     # ≤100字
    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 公司模型（巴菲特标准 — Layer 1 提取 + 三表）
# ===========================================================================


class CompanyProfile(SQLModel, table=True):
    """公司档案 — 最小标识"""

    __tablename__ = "company_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)                  # "台积电" / "贵州茅台"
    ticker: str = Field(index=True, unique=True)   # "TSM" / "600519.SH"
    market: str                                    # us|cn_a|cn_h|hk|jp|...
    industry: Optional[str] = None                 # 所属行业
    summary: Optional[str] = None                  # 一句话商业模式
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CompanyNarrative(SQLModel, table=True):
    """公司叙事 — 管理层承诺：故事 + 资金量 + 预期结果"""

    __tablename__ = "company_narratives"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )

    narrative: str                                 # 故事本身 ≤300字
    capital_required: Optional[float] = None       # 资金量
    capital_unit: Optional[str] = None             # 亿美元|亿人民币|...
    promised_outcome: Optional[str] = None         # 承诺结果 ≤200字
    deadline: Optional[date] = None                # 承诺时间
    status: str = "announced"                      # announced|in_progress|delivered|missed|abandoned

    reported_at: Optional[date] = None             # 发布时间
    created_at: datetime = Field(default_factory=_utcnow)


class OperationalIssue(SQLModel, table=True):
    """经营议题 — CEO信/MD&A 中的定性经营问题，每行一个议题

    表现/归因/风险/指引 是同一议题的四个维度：
    - 表现：定性描述（不含财务数字，数字在三表）
    - 归因：管理层对该表现的解释
    - 风险：该议题面临的风险
    - 指引：管理层对未来的展望/指引
    """

    __tablename__ = "operational_issues"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "2024Q4" / "2024FY"
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )

    topic: str                                     # 议题名 ≤30字
    performance: Optional[str] = None              # 表现（定性）≤200字
    attribution: Optional[str] = None              # 归因 ≤200字
    risk: Optional[str] = None                     # 风险 ≤200字
    guidance: Optional[str] = None                 # 指引 ≤200字

    created_at: datetime = Field(default_factory=_utcnow)


class FinancialStatement(SQLModel, table=True):
    """财务报表 — 一份报表一条记录"""

    __tablename__ = "financial_statements"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "2024Q4" / "2024FY"
    period_type: str                               # quarterly|annual
    statement_type: str = Field(index=True)        # income|balance_sheet|cashflow|equity|tax_detail|sbc_detail
    currency: str = "CNY"                          # CNY|USD|HKD|...
    reported_at: Optional[date] = None             # 报告发布日期
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class FinancialLineItem(SQLModel, table=True):
    """财务科目明细 — 一个科目一条记录（长表设计，任意科目可存）"""

    __tablename__ = "financial_line_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    statement_id: int = Field(foreign_key="financial_statements.id", index=True)
    item_key: str = Field(index=True)              # 标准化键: "revenue" / "operating_income" / "ppe_net"
    item_label: str                                # 原始标签: "营业收入" / "Revenue"
    value: float                                   # 数值
    parent_key: Optional[str] = None               # 父科目键（层级结构）
    ordinal: int = 0                               # 原报表中的排列顺序
    note: Optional[str] = None                     # 备注（重述、调整等）


class DownstreamSegment(SQLModel, table=True):
    """下游 — 客户或收入流，每期一行，segment 可选(null=公司级)"""

    __tablename__ = "downstream_segments"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2025"
    segment: Optional[str] = None                  # 业务线: "Compute & Networking" / null=公司级
    customer_name: str                             # 客户名/收入流名（如 "Customer A" 或 "Data Center GPU Hardware"）
    customer_type: Optional[str] = None            # direct|indirect|channel|OEM|distributor
    products: Optional[str] = None                 # 卖给该客户的产品/服务
    channels: Optional[str] = None                 # 销售渠道: OEM/直销/分销/云市场
    revenue: Optional[float] = None                # 收入（百万美元）
    revenue_pct: Optional[float] = None            # 占总收入百分比
    growth_yoy: Optional[str] = None               # 同比增速描述
    backlog: Optional[float] = None                # 欠交付订单金额（百万美元）
    backlog_note: Optional[str] = None             # 积压订单说明
    pricing_model: Optional[str] = None            # per-unit|per-user/month|usage-based|混合
    contract_duration: Optional[str] = None        # one-time|1-year|multi-year
    revenue_type: Optional[str] = None             # product_sale|subscription|license|royalty|service|NRE|cloud_service
    is_recurring: Optional[bool] = None            # 是否经常性收入
    recognition_method: Optional[str] = None       # point_in_time|over_time
    # Axion 新增字段（v10）
    contract_duration_months: Optional[int] = None  # 合同平均时长（月）
    switching_cost_level: Optional[str] = None       # high|medium|low（客户转换成本）
    description: Optional[str] = None              # 补充说明
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class UpstreamSegment(SQLModel, table=True):
    """上游 — 每个供应商每期一行，segment 可选(null=公司级)"""

    __tablename__ = "upstream_segments"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2025"
    segment: Optional[str] = None                  # 业务线: "Compute & Networking" / null=公司级
    supplier_name: str                             # 供应商名（每个供应商单独一行）
    supply_type: str                               # foundry|memory|assembly_test|substrate|component|contract_mfg|software|logistics
    material_or_service: Optional[str] = None      # 具体供应内容: "5nm晶圆代工" / "HBM3e"
    process_node: Optional[str] = None             # 制程节点: "4nm" / "5nm"（如适用）
    geographic_location: Optional[str] = None      # 供应商/工厂所在地
    is_sole_source: bool = False                   # 是否独家供应
    purchase_obligation: Optional[float] = None    # 采购义务金额（百万美元）
    lead_time: Optional[str] = None                # 交货周期: "exceeding 12 months"
    contract_type: Optional[str] = None            # 长期合约|purchase_order|prepaid|non-cancellable
    prepaid_amount: Optional[float] = None         # 预付金额（百万美元）
    concentration_risk: Optional[str] = None       # 集中度风险描述
    description: Optional[str] = None              # 补充说明
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class GeographicRevenue(SQLModel, table=True):
    """地域收入 — 每期每地域一行"""

    __tablename__ = "geographic_revenues"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2026"
    region: str                                    # "United States" / "China" / "Taiwan" / "EMEA"
    revenue: Optional[float] = None                # 收入（百万）
    revenue_share: Optional[float] = None          # 占比（0-1）
    growth_yoy: Optional[str] = None               # 同比增速
    note: Optional[str] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class NonFinancialKPI(SQLModel, table=True):
    """非财务KPI — 每期每指标一行"""

    __tablename__ = "non_financial_kpis"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2026"
    kpi_name: str                                  # "员工总数" / "数据中心客户数" / "专利数量"
    kpi_value: str                                 # "32,000" / "40,000+" — 文本，兼容非数值
    kpi_unit: Optional[str] = None                 # "人" / "个" / "件"
    yoy_change: Optional[str] = None               # "+15%" / "持平"
    category: Optional[str] = None                 # workforce|customer|product|esg|operational
    note: Optional[str] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class DebtObligation(SQLModel, table=True):
    """债务/义务明细 — 每条债务工具一行（贷款、债券、租赁、可转债等）"""

    __tablename__ = "debt_obligations"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2026" — 报告所属期
    instrument_name: str                           # "1.55% Notes due 2028"
    debt_type: str                                 # bond|loan|lease|convertible|credit_facility
    principal: Optional[float] = None               # 本金/余额（百万）
    currency: str = "USD"
    interest_rate: Optional[float] = None          # 年利率（如 0.0155 = 1.55%）
    maturity_date: Optional[date] = None           # 到期日
    is_secured: bool = False                       # 是否有担保
    is_current: bool = False                       # 是否一年内到期
    is_floating_rate: bool = False                 # 是否浮动利率（Axion v10）
    note: Optional[str] = None                     # 特殊条款备注
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class Litigation(SQLModel, table=True):
    """诉讼/或有事项 — 每个案件一行"""

    __tablename__ = "litigations"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    case_name: str                                 # 案件名称
    case_type: str                                 # lawsuit|regulatory|patent|antitrust|environmental|tax|other
    status: str = "pending"                        # pending|settled|dismissed|ongoing|appealed
    counterparty: Optional[str] = None             # 对方（原告/监管机构）
    filed_at: Optional[date] = None                # 立案日期
    claimed_amount: Optional[float] = None         # 索赔金额（百万）
    accrued_amount: Optional[float] = None         # 已计提金额（百万）
    currency: str = "USD"
    description: Optional[str] = None              # 案情摘要 ≤300字
    resolution: Optional[str] = None               # 结果 ≤200字
    resolved_at: Optional[date] = None             # 结案日期
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class ExecutiveCompensation(SQLModel, table=True):
    """管理层/董事薪酬 — 每人每期一行，role_type 区分高管与董事"""

    __tablename__ = "executive_compensations"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2025"
    role_type: str = "executive"                   # executive|director
    name: str                                      # 姓名
    title: str = ""                                # 身份: CEO|CFO|EVP Operations|Independent Director|Audit Committee Chair|...
    base_salary: Optional[float] = None            # 基本工资（董事: fees_earned_cash）
    bonus: Optional[float] = None                  # 现金奖金
    stock_awards: Optional[float] = None           # 股票奖励（公允价值）
    option_awards: Optional[float] = None          # 期权奖励（公允价值）
    non_equity_incentive: Optional[float] = None   # 非股权激励
    other_comp: Optional[float] = None             # 其他补偿
    total_comp: Optional[float] = None             # 总薪酬
    currency: str = "USD"
    # CEO Pay Ratio（仅 CEO 行填写）
    pay_ratio: Optional[float] = None              # CEO/员工中位数比值（如 166）
    median_employee_comp: Optional[float] = None   # 员工中位数薪酬
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class StockOwnership(SQLModel, table=True):
    """持股信息 — 管理层/大股东持股，每人每期一行"""

    __tablename__ = "stock_ownership"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2025"
    name: str                                      # 持有人姓名
    title: Optional[str] = None                    # 职位/身份
    shares_beneficially_owned: Optional[int] = None  # 受益持股数
    percent_of_class: Optional[float] = None       # 持股比例（0-100）
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class RelatedPartyTransaction(SQLModel, table=True):
    """关联交易 — 每笔交易一行"""

    __tablename__ = "related_party_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)                # "FY2026"
    related_party: str                             # 关联方名称
    relationship: str                              # 关联关系: director|officer|major_shareholder|subsidiary|affiliate|family
    transaction_type: str                          # sale|purchase|lease|loan|guarantee|service|license|other
    amount: Optional[float] = None                 # 金额（百万）
    currency: str = "USD"
    terms: Optional[str] = None                    # 交易条件/定价依据 ≤200字
    is_ongoing: bool = False                       # 是否持续性交易
    description: Optional[str] = None              # 交易说明 ≤300字
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class TechInsight(SQLModel, table=True):
    """技术洞察 — 问题→方案→效果→局限，一行一个问题（论文+专利共用）

    next_problem_id 构成技术演进 DAG：
      局限A → 成为下一个问题B → 局限B → 成为下一个问题C ...
    """

    __tablename__ = "tech_insights"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    source_type: str = "paper"                     # paper|patent
    technology_domain: Optional[str] = None        # 技术领域标签: GPU|HBM|interconnect|packaging|...
    problem: str                                   # 问题/瓶颈 ≤300字
    solutions_json: Optional[str] = None           # JSON list[str] — 方案列表
    effects_json: Optional[str] = None             # JSON list[str] — 效果/性能列表
    limitations_json: Optional[str] = None         # JSON list[str] — 局限列表（专利常为空）
    # 技术演进链：指向因本条局限而产生的下一个问题
    next_problem_id: Optional[int] = Field(
        default=None, foreign_key="tech_insights.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class PatentRight(SQLModel, table=True):
    """专利法律权利 — 每个专利一行"""

    __tablename__ = "patent_rights"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    patent_number: Optional[str] = None            # 专利号: "US11562247B2"
    title: Optional[str] = None                    # 专利标题
    claims_summary: Optional[str] = None           # 权利要求摘要 ≤500字
    claims_count: Optional[int] = None             # 权利要求总数
    prior_art_json: Optional[str] = None           # JSON list[str] — 引用的专利号/论文
    assignee: Optional[str] = None                 # 专利权人
    inventors: Optional[str] = None                # 发明人（逗号分隔）
    filing_date: Optional[date] = None             # 申请日
    priority_date: Optional[date] = None           # 优先权日
    expiry_date: Optional[date] = None             # 到期日
    legal_status: Optional[str] = None             # active|expired|pending|abandoned
    patent_family_json: Optional[str] = None       # JSON list — 专利家族
    classification: Optional[str] = None           # CPC/IPC 分类号
    created_at: datetime = Field(default_factory=_utcnow)


class PatentCommercial(SQLModel, table=True):
    """专利商业化 — 每个被许可方/商业化事件一行"""

    __tablename__ = "patent_commercials"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    patent_number: Optional[str] = None            # 关联专利号
    event_type: str = "license"                    # license|litigation|cross_license|FRAND|sale|pool
    counterparty: Optional[str] = None             # 被许可方/诉讼对手/买方
    amount: Optional[float] = None                 # 金额（百万美元）
    rate: Optional[str] = None                     # 费率描述（如 "2.275% of device price"）
    license_type: Optional[str] = None             # exclusive|non-exclusive|cross_license|FRAND
    territory: Optional[str] = None                # 许可地域
    duration: Optional[str] = None                 # 许可期限
    status: Optional[str] = None                   # active|expired|pending|settled|terminated
    source: Optional[str] = None                   # 信息来源: 10-K|press_release|court_filing
    description: Optional[str] = None              # 补充说明
    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# Axion 数据契约 — 巴菲特模块新增表（v10）
# ===========================================================================


class PricingAction(SQLModel, table=True):
    """定价行为记录 — 提价/降价事件，每次定价变化一行"""

    __tablename__ = "pricing_actions"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)
    product_or_segment: str
    price_change_pct: Optional[float] = None
    volume_impact_pct: Optional[float] = None
    effective_date: Optional[date] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class CompetitorRelation(SQLModel, table=True):
    """竞对关系 — 每对竞争关系一行"""

    __tablename__ = "competitor_relations"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    competitor_name: str
    competitor_company_id: Optional[int] = Field(
        default=None, foreign_key="company_profiles.id"
    )
    market_segment: Optional[str] = None
    relationship_type: str = "direct_competitor"  # direct_competitor|indirect_competitor|potential_entrant
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class MarketShareData(SQLModel, table=True):
    """市占率数据 — 每家公司每期每细分市场一行"""

    __tablename__ = "market_share_data"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    market_segment: str
    period: str = Field(index=True)
    share_pct: Optional[float] = None
    source_description: Optional[str] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class KnownIssue(SQLModel, table=True):
    """已知问题清单 — 外部可识别的公司问题"""

    __tablename__ = "known_issues"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)
    issue_description: str
    issue_category: str = "operational"  # financial|operational|legal|reputational|regulatory
    severity: str = "major"              # critical|major|minor
    source_type: str = "news"            # analyst_report|news|litigation|financial_anomaly
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class ManagementAcknowledgment(SQLModel, table=True):
    """管理层问题回应 — 管理层对已知问题的提及或回应"""

    __tablename__ = "management_acknowledgments"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)
    known_issue_id: Optional[int] = Field(
        default=None, foreign_key="known_issues.id"
    )
    issue_description: Optional[str] = None
    response_quality: str = "forthright"  # forthright|downplay|deflect|deny
    has_action_plan: bool = False
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class InsiderTransaction(SQLModel, table=True):
    """内部人交易 — SEC Form 4 / 权益披露"""

    __tablename__ = "insider_transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    person_name: str
    title: Optional[str] = None
    transaction_type: str = "buy"        # buy|sell|option_exercise
    shares: Optional[int] = None
    price_per_share: Optional[float] = None
    transaction_date: Optional[date] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class ExecutiveChange(SQLModel, table=True):
    """高管变动 — 入职/离职/晋升/降职"""

    __tablename__ = "executive_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    person_name: str
    title: Optional[str] = None
    change_type: str = "joined"          # joined|departed|promoted|demoted
    change_date: Optional[date] = None
    reason: Optional[str] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class AuditOpinion(SQLModel, table=True):
    """审计意见 — 每个财年一行"""

    __tablename__ = "audit_opinions"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)
    opinion_type: str = "unqualified"    # unqualified|qualified|adverse|disclaimer
    auditor_name: Optional[str] = None
    emphasis_matters: Optional[str] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class AnalystEstimate(SQLModel, table=True):
    """分析师预期 — 一致预期 vs 实际"""

    __tablename__ = "analyst_estimates"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    period: str = Field(index=True)
    metric: str                          # eps|revenue|ebitda
    consensus_estimate: Optional[float] = None
    actual: Optional[float] = None
    surprise_pct: Optional[float] = None
    estimate_date: Optional[date] = None
    created_at: datetime = Field(default_factory=_utcnow)


class EquityOffering(SQLModel, table=True):
    """股权融资事件 — IPO/增发/可转债"""

    __tablename__ = "equity_offerings"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    offering_date: Optional[date] = None
    offering_type: str = "secondary"     # ipo|secondary|follow_on|atm|convertible
    shares_offered: Optional[int] = None
    price_per_share: Optional[float] = None
    total_proceeds: Optional[float] = None
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


class ManagementGuidance(SQLModel, table=True):
    """管理层前瞻指引 — 每条 guidance 一行"""

    __tablename__ = "management_guidance"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company_profiles.id", index=True)
    source_period: str = Field(index=True)   # 发布期: "FY2025Q3"
    target_period: Optional[str] = None      # 目标期: "FY2026"
    metric: str                              # revenue|revenue_growth|operating_margin|net_margin|eps|capex|roic_target|free_cash_flow|gross_margin|tax_rate|share_repurchase|dividend|other
    value_low: Optional[float] = None
    value_high: Optional[float] = None
    unit: str = "absolute"                   # pct|absolute|per_share
    confidence_language: Optional[str] = None  # expect|target|aspire|preliminary
    verbatim: Optional[str] = None           # 原文引用
    raw_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id", index=True
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 旧表 class 定义 — 注释掉，DB 中旧表数据保留只读
# ===========================================================================
# v8 通用提取表（已迁移到域专用管线）：
#   ExtractionNode, ExtractionEdge, DOMAIN_NODE_TYPES
# v7 及更早的旧实体表：
#   Fact, Assumption, ImplicitCondition, Conclusion, Prediction,
#   Solution, Theory
# 旧专用表：PolicyTheme, PolicyItem, Policy, PolicyMeasure,
#           Issue, TechRoute, Metric, PaperAnalysis, EarningsAnalysis
# 旧边表：  EntityRelationship, EdgeType enum
# Axion 表：CanonicalPlayer, PlayerAlias, SupplyNode, LayerSchema
#           (不再 re-export，Axion 直接管理)
