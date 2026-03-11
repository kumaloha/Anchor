"""
Anchor 核心数据模型（v5 — 七实体 + 显式关系边表）
=================================================
七实体：
  Fact（事实依据）       — fact_verdict: credible|vague|unreliable|unavailable
  Assumption（假设条件） — assumption_verdict: high_probability|medium_probability|low_probability|unavailable
  ImplicitCondition     — implicit_verdict: consensus|contested|false
  Conclusion（结论）     — conclusion_verdict: confirmed|refuted|partial|unverifiable|pending
  Prediction（预测）     — prediction_verdict: pending|accurate|directional|off_target|wrong
  Solution（解决方案）   — 不验证
  Theory（理论框架）     — 不验证

边表：
  EntityRelationship（relationships）— 显式关系边，取代 Logic 的 JSON 数组

保留基础设施表（不变）：
  AuthorGroup / Topic / Author / MonitoredSource / RawPost
  PostQualityAssessment / AuthorStanceProfile / AuthorStats
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# 枚举
# ===========================================================================


class EdgeType(str, Enum):
    FACT_SUPPORTS_CONCLUSION = "fact_supports_conclusion"
    ASSUMPTION_CONDITIONS_CONCLUSION = "assumption_conditions_conclusion"
    IMPLICIT_CONDITIONS_CONCLUSION = "implicit_conditions_conclusion"
    CONCLUSION_SUPPORTS_CONCLUSION = "conclusion_supports_conclusion"
    CONCLUSION_LEADS_TO_PREDICTION = "conclusion_leads_to_prediction"
    CONCLUSION_ENABLES_SOLUTION = "conclusion_enables_solution"
    POLICY_SUPPORTS_CONCLUSION = "policy_supports_conclusion"
    FACT_SUPPORTS_THEORY = "fact_supports_theory"
    CONCLUSION_SUPPORTS_THEORY = "conclusion_supports_theory"
    THEORY_SUPPORTS_THEORY = "theory_supports_theory"
    THEORY_SUPPORTS_CONCLUSION = "theory_supports_conclusion"
    THEORY_LEADS_TO_PREDICTION = "theory_leads_to_prediction"
    THEORY_ENABLES_SOLUTION = "theory_enables_solution"
    # 产业链研究扩展
    PLAYER_DOMINATES_NODE = "player_dominates_node"
    PLAYER_ENTERS_NODE = "player_enters_node"
    ISSUE_CASCADES_ISSUE = "issue_cascades_issue"
    ISSUE_BLOCKS_NODE = "issue_blocks_node"
    ISSUE_CONSTRAINS_PLAYER = "issue_constrains_player"
    TECHROUTE_MITIGATES_ISSUE = "techroute_mitigates_issue"
    TECHROUTE_COMPETES_TECHROUTE = "techroute_competes_techroute"
    METRIC_EVIDENCES_ISSUE = "metric_evidences_issue"
    FACT_SUPPORTS_ISSUE = "fact_supports_issue"
    CONCLUSION_ABOUT_PLAYER = "conclusion_about_player"
    CONCLUSION_ABOUT_NODE = "conclusion_about_node"


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
    content_summary: Optional[str] = None       # Chain1 Step5 叙事摘要

    # 政策文档专属字段（Chain 1 policy 模式写入）
    issuing_authority: Optional[str] = None     # 发文机关（如"国务院"）
    authority_level: Optional[str] = None       # 顶层设计|部委联合|部委独立

    # Chain 2 — 内容分类与作者意图
    notion_page_id: Optional[str] = None        # Notion 页面 ID（同步后写回）
    content_type: Optional[str] = None          # 财经分析|市场动向|产业链研究|公司调研|技术论文|政策解读
    content_type_secondary: Optional[str] = None  # 次分类（可选）
    content_subtype: Optional[str] = None       # 财经分析子分类：市场分析|地缘分析|政策分析|技术影响|混合分析
    content_topic: Optional[str] = None         # 具体主题（≤30字）
    author_intent: Optional[str] = None         # 传递信息|影响观点|警示风险|推荐行动|教育科普|引发讨论|推广宣传|政治动员
    intent_note: Optional[str] = None           # 意图说明（≤100字）
    policy_delta: Optional[str] = None          # 政策对比：与上一年同类政策的核心变化（≤150字）
    chain2_analyzed: bool = False               # 是否已完成 Chain2 分析
    chain2_analyzed_at: Optional[datetime] = None

    is_duplicate: bool = False
    original_post_id: Optional[int] = Field(
        default=None, foreign_key="raw_posts.id"
    )

    monitored_source_id: Optional[int] = Field(
        default=None, foreign_key="monitored_sources.id"
    )


# ===========================================================================
# 六实体（v4）
# ===========================================================================


class Fact(SQLModel, table=True):
    """事实依据 — 可独立核查的客观陈述

    Chain 3 验证后写入 fact_verdict。
    """

    __tablename__ = "facts"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字，高度抽象，用于展示）
    claim: str                                   # 原文陈述（≤120字）
    verifiable_statement: Optional[str] = None   # 单句可核实陈述（供 Chain3 使用）
    temporal_type: str = "retrospective"         # retrospective | predictive
    temporal_note: Optional[str] = None          # 时间范围标注

    # Chain 3 填写
    fact_verdict: Optional[str] = None           # credible | vague | unreliable | unavailable
    verdict_evidence: Optional[str] = None       # 证据摘要（≤200字）
    verdict_verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)


class Assumption(SQLModel, table=True):
    """假设条件 — 作者明确陈述的"如果X则..."前提

    Chain 3 评估该假设成立概率，写入 assumption_verdict。
    """

    __tablename__ = "assumptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    condition_text: str                          # 假设条件陈述（≤120字）
    verifiable_statement: Optional[str] = None   # 单句可核实表达
    temporal_note: Optional[str] = None

    # Chain 3 填写
    assumption_verdict: Optional[str] = None     # high_probability | medium_probability | low_probability | unavailable
    verdict_evidence: Optional[str] = None
    verdict_verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)


class ImplicitCondition(SQLModel, table=True):
    """隐含条件 — 推理中未说出但依赖的暗含前提

    is_obvious_consensus=True 时 Chain3 直接写 consensus，跳过 LLM。
    Chain 3 评估是否为普遍共识，写入 implicit_verdict。
    """

    __tablename__ = "implicit_conditions"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    condition_text: str                          # 隐含条件陈述（≤120字）
    is_obvious_consensus: bool = False           # 显而易见的共识，Chain3 可直接跳过

    # Chain 3 填写
    implicit_verdict: Optional[str] = None       # consensus | contested | false
    verdict_evidence: Optional[str] = None
    verdict_verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)


class Conclusion(SQLModel, table=True):
    """结论 — 作者对已发生事件或当前形势的判断（回顾型）

    Chain 1 从 Relationship 表读取支撑实体，Chain 3 推导并写入 conclusion_verdict。
    """

    __tablename__ = "conclusions"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)
    author_id: Optional[int] = Field(default=None, foreign_key="authors.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    claim: str                                   # 结论陈述（≤120字）
    verifiable_statement: Optional[str] = None   # 单句可核实陈述

    # Chain 1 提取
    author_confidence: Optional[str] = None      # certain | likely | uncertain | speculative
    is_core_conclusion: bool = False             # DAG 叶子节点（无出边到其他结论）
    is_in_cycle: bool = False                    # DFS 检测到循环

    # Chain 3 填写
    conclusion_verdict: Optional[str] = None     # confirmed | refuted | partial | unverifiable | pending
    verdict_trace: Optional[str] = None          # JSON 推导轨迹

    created_at: datetime = Field(default_factory=_utcnow)


class Prediction(SQLModel, table=True):
    """预测 — 作者对未来事件或趋势的判断

    temporal_validity 由 Chain 1 根据 temporal_note 是否存在自动标注。
    Chain 3 在 monitoring_end 到达后验证，写入 prediction_verdict。
    """

    __tablename__ = "predictions"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)
    author_id: Optional[int] = Field(default=None, foreign_key="authors.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    claim: str                                   # 预测陈述（≤120字）
    temporal_note: Optional[str] = None          # 时间范围（如"2026-2030年"）；None 表示无时效
    temporal_validity: str = "no_timeframe"      # has_timeframe | no_timeframe（Chain1 自动标注）

    # Chain 1 提取
    author_confidence: Optional[str] = None      # certain | likely | uncertain | speculative

    # Chain 3 监控配置
    monitoring_start: Optional[datetime] = None
    monitoring_end: Optional[datetime] = None

    # Chain 3 填写
    prediction_verdict: Optional[str] = None     # pending | accurate | directional | off_target | wrong
    verdict_evidence: Optional[str] = None
    verdict_verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)


class Solution(SQLModel, table=True):
    """解决方案 — 作者建议的行动方案（不验证）"""

    __tablename__ = "solutions"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)
    author_id: Optional[int] = Field(default=None, foreign_key="authors.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    claim: str                                   # 建议内容（≤120字）
    action_type: Optional[str] = None           # buy|sell|hold|short|diversify|hedge|reduce|advocate
    action_target: Optional[str] = None         # 标的物（如"黄金ETF"、"美国10年期国债"）
    action_rationale: Optional[str] = None      # 此建议如何从结论推导

    created_at: datetime = Field(default_factory=_utcnow)


class Theory(SQLModel, table=True):
    """理论框架 — 作者建立的模型/理论/原则（不验证）

    Theory 是作者用来推演预测和行动建议的理论框架，
    它由 Fact/Conclusion 支撑，向下推出 Prediction/Solution。
    """

    __tablename__ = "theories"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)
    author_id: Optional[int] = Field(default=None, foreign_key="authors.id", index=True)

    summary: Optional[str] = None                # 一句话摘要（≤15字）
    claim: str                                   # 理论框架陈述（≤120字）

    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 政策专属表（policy 模式，仅在 content_mode="policy" 时写入）
# ===========================================================================


class PolicyTheme(SQLModel, table=True):
    """政策主旨 — 政策文档的大类主题（民生/产业/军事/财政等）"""

    __tablename__ = "policy_themes"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)

    theme_name: str                              # 民生 / 产业 / 军事 / 财政 / 外贸 / 科技 ...
    background: Optional[str] = None            # 背景与目的（≤200字）
    enforcement_note: Optional[str] = None      # 组织保障描述（≤80字）
    has_enforcement_teeth: bool = False          # 是否纳入考核/有执行主体

    created_at: datetime = Field(default_factory=_utcnow)


class PolicyItem(SQLModel, table=True):
    """政策条目 — 每条政策承诺/计划（替代 policy 模式下误用的 Prediction）"""

    __tablename__ = "policy_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    policy_theme_id: Optional[int] = Field(default=None, foreign_key="policy_themes.id")

    summary: str                                 # ≤15字摘要
    policy_text: str                             # 政策内容（≤120字）
    urgency: str                                 # mandatory|encouraged|pilot|gradual
    change_type: Optional[str] = None           # 新增|调整|延续（由比对步骤填写，提取时为 null）
    change_note: Optional[str] = None           # ≤30字变化说明（比对步骤填写）
    metric_value: Optional[str] = None          # 量化值（如 "4%", "1.3万亿"）
    target_year: Optional[str] = None           # 目标年份（如 "2026"）
    is_hard_target: bool = False                 # 是否量化硬约束

    # Chain 3 执行追踪
    execution_status: Optional[str] = None      # implemented|in_progress|stalled|not_started|unknown
    execution_note: Optional[str] = None        # ≤80字执行情况说明（含来源依据）

    created_at: datetime = Field(default_factory=_utcnow)


class Policy(SQLModel, table=True):
    """政策（v3）— 六维属性政策主旨，含手段子条目 + 内嵌同比对比"""

    __tablename__ = "policies"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)

    theme: str                               # 主旨 ≤8字（如"绿色低碳"）
    change_summary: Optional[str] = None    # 一句话变化总结 ≤50字
    target: Optional[str] = None            # 当年目标
    target_prev: Optional[str] = None       # 上年目标
    intensity: Optional[str] = None         # strong|moderate|weak
    intensity_prev: Optional[str] = None    # 上年 intensity
    intensity_note: Optional[str] = None    # 当年力度说明 ≤60字
    intensity_note_prev: Optional[str] = None  # 上年力度说明 ≤60字
    background: Optional[str] = None        # 当年背景 ≤200字
    background_prev: Optional[str] = None   # 上年背景 ≤200字
    organization: Optional[str] = None      # 当年组织保障 ≤100字
    organization_prev: Optional[str] = None # 上年组织保障 ≤100字

    created_at: datetime = Field(default_factory=_utcnow)


class PolicyMeasure(SQLModel, table=True):
    """政策手段（v3）— Policy 下的具体措施子条目"""

    __tablename__ = "policy_measures"

    id: Optional[int] = Field(default=None, primary_key=True)
    policy_id: int = Field(foreign_key="policies.id", index=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)

    summary: str                             # ≤15字摘要
    measure_text: str                        # 具体措施 ≤150字
    trend: Optional[str] = None             # 升级|降级|延续|新增|删除
    trend_note: Optional[str] = None        # 变化说明 ≤30字

    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 产业链研究表（industry 模式，仅在 content_mode="industry" 时写入）
# ===========================================================================


class CanonicalPlayer(SQLModel, table=True):
    """产业玩家（跨文章归一化）"""

    __tablename__ = "canonical_players"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_name: str = Field(unique=True, index=True)
    entity_type: Optional[str] = None       # company|government|research_institute|alliance|startup
    headquarters: Optional[str] = None
    description: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PlayerAlias(SQLModel, table=True):
    """玩家别名（用于归一化匹配）"""

    __tablename__ = "player_aliases"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_player_id: int = Field(foreign_key="canonical_players.id", index=True)
    alias: str = Field(index=True)
    language: Optional[str] = None           # zh|en|ja|...

    created_at: datetime = Field(default_factory=_utcnow)


class SupplyNode(SQLModel, table=True):
    """供应链节点（跨文章去重）"""

    __tablename__ = "supply_nodes"

    id: Optional[int] = Field(default=None, primary_key=True)
    industry_chain: str = Field(index=True)  # 如 "AI"
    tier_id: int                             # 层级编号
    layer_name: str                          # 层级名（如 "算力芯片"）
    node_name: str                           # 节点名（如 "GPU 设计"）
    description: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)

    class Config:
        table_args = {"UniqueConstraint": ("industry_chain", "tier_id", "node_name")}


class LayerSchema(SQLModel, table=True):
    """层级指标定义（种子数据 + 运行时扩展）"""

    __tablename__ = "layer_schemas"

    id: Optional[int] = Field(default=None, primary_key=True)
    industry_chain: str = Field(index=True)
    tier_id: int
    metric_name: str
    unit: Optional[str] = None
    description: Optional[str] = None

    created_at: datetime = Field(default_factory=_utcnow)

    class Config:
        table_args = {"UniqueConstraint": ("industry_chain", "tier_id", "metric_name")}


class Issue(SQLModel, table=True):
    """产业议题（per-article）"""

    __tablename__ = "issues"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    supply_node_id: Optional[int] = Field(default=None, foreign_key="supply_nodes.id")

    issue_text: str                          # 议题描述（≤150字）
    severity: Optional[str] = None           # critical|high|medium|low
    status: Optional[str] = None             # active|emerging|resolved
    resolution_progress: Optional[str] = None  # 进展描述（≤80字）
    summary: Optional[str] = None            # ≤15字摘要

    created_at: datetime = Field(default_factory=_utcnow)


class TechRoute(SQLModel, table=True):
    """技术路线（per-article）"""

    __tablename__ = "tech_routes"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    supply_node_id: Optional[int] = Field(default=None, foreign_key="supply_nodes.id")

    route_name: str                          # 技术路线名（如 "CoWoS 封装"）
    maturity: Optional[str] = None           # experimental|emerging|growth|mature|declining
    competing_routes: Optional[str] = None   # JSON array of route names
    summary: Optional[str] = None            # ≤15字摘要

    created_at: datetime = Field(default_factory=_utcnow)


class Metric(SQLModel, table=True):
    """产业指标数据点（per-article）"""

    __tablename__ = "metrics"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    supply_node_id: Optional[int] = Field(default=None, foreign_key="supply_nodes.id")
    canonical_player_id: Optional[int] = Field(default=None, foreign_key="canonical_players.id")

    metric_name: str
    metric_value: str
    unit: Optional[str] = None
    time_reference: Optional[str] = None     # 如 "2025Q4", "2026年"
    evidence_score: Optional[float] = None   # 0-1
    is_schema_metric: bool = False           # 是否匹配 LayerSchema 预定义指标

    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 关系边表（v4，取代 Logic 的 JSON 数组）
# ===========================================================================


class EntityRelationship(SQLModel, table=True):
    """实体关系边 — 显式记录七实体间的有向关系

    source_type / target_type 取值：
      fact | assumption | implicit_condition | conclusion | prediction | solution | theory

    edge_type 取值（EdgeType 枚举）：
      fact_supports_conclusion
      assumption_conditions_conclusion
      implicit_conditions_conclusion
      conclusion_supports_conclusion
      conclusion_leads_to_prediction
      conclusion_enables_solution
      fact_supports_theory
      conclusion_supports_theory
      theory_supports_theory
      theory_supports_conclusion
      theory_leads_to_prediction
      theory_enables_solution
    """

    __tablename__ = "relationships"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id", index=True)

    source_type: str = Field(index=True)         # 源实体类型
    source_id: int = Field(index=True)           # 源实体 ID
    target_type: str = Field(index=True)         # 目标实体类型
    target_id: int = Field(index=True)           # 目标实体 ID
    edge_type: str                               # EdgeType 枚举值

    note: Optional[str] = None                  # 补充说明（≤80字）
    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 评估与统计表（保留，Chain 2/3 使用）
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
    """作者立场分布档案（Chain 2 写入）"""

    __tablename__ = "author_stance_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int = Field(foreign_key="authors.id", unique=True, index=True)

    # JSON dict: {"看涨/多头": 5, "看跌/空头": 2, ...}
    stance_distribution: Optional[str] = None
    dominant_stance: Optional[str] = None
    dominant_stance_ratio: Optional[float] = None
    total_analyzed: int = 0

    # Chain 2 LLM 分析结果（基于近期帖子聚合）
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
