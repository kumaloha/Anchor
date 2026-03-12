"""
Anchor 核心数据模型（v8 — 通用节点+边架构）
=============================================
Node（节点）— 6 领域 × 各自节点类型
Edge（边）  — 节点间关系，记录来源文章

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
# 节点类型注册表（Python dict，不在 DB 中）
# ===========================================================================

DOMAIN_NODE_TYPES = {
    "policy":     ["主旨", "目标", "战略", "战术", "资源", "考核", "约束", "反馈", "外溢"],
    "industry":   ["格局", "驱动", "趋势", "技术路线", "资金流向", "机会威胁", "标的"],
    "technology": ["问题", "方案", "效果性能", "局限场景", "玩家"],
    "futures":    ["供给", "需求", "库存", "头寸", "冲击", "缺口"],
    "company":    ["表现", "归因", "指引", "风险", "叙事"],
    "expert":     ["事实", "判断", "预测", "建议"],
}


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
# v8 Node + Edge 表
# ===========================================================================


class Node(SQLModel, table=True):
    """通用节点 — 所有领域的提取实体统一存储"""

    __tablename__ = "nodes"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    domain: str = Field(index=True)       # policy|industry|technology|futures|company|expert
    node_type: str = Field(index=True)    # 主旨|目标|... 领域内的节点类型
    claim: str                             # 主要内容 (≤300字符)
    summary: str                           # 短摘要 (≤30字符)
    abstract: Optional[str] = None         # 一句话总结 (≤100字符)
    metadata_json: Optional[str] = None    # 领域特定扩展数据 (JSON)
    verdict: Optional[str] = None          # 验证结论
    verdict_evidence: Optional[str] = None
    verdict_verified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Edge(SQLModel, table=True):
    """通用边 — 节点间关系"""

    __tablename__ = "edges"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_node_id: int = Field(foreign_key="nodes.id", index=True)
    target_node_id: int = Field(foreign_key="nodes.id", index=True)
    edge_type: str = "connected"          # 先简单连接，后续加类型
    note: Optional[str] = None            # ≤80字说明
    added_by_post_id: int = Field(foreign_key="raw_posts.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)


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
# 旧表 class 定义（v7 及之前）— 注释掉，DB 中旧表数据保留只读
# ===========================================================================
# 旧实体表：Fact, Assumption, ImplicitCondition, Conclusion, Prediction,
#           Solution, Theory
# 旧专用表：PolicyTheme, PolicyItem, Policy, PolicyMeasure,
#           Issue, TechRoute, Metric, PaperAnalysis, EarningsAnalysis
# 旧边表：  EntityRelationship, EdgeType enum
# Axion 表：CanonicalPlayer, PlayerAlias, SupplyNode, LayerSchema
#           (不再 re-export，Axion 直接管理)
