"""
Anchor 核心数据模型
==================
四类核心实体（v3）：
  Fact（事实依据）— Layer2 提取，Layer4 现实对齐
  Conclusion（结论）— 回顾型 + 预测型（含 is_core_conclusion）
  Condition（条件）— 统一假设条件 + 隐含条件
  Solution（解决方案）— 作者建议的行动方案（不验证裁定）
  Logic（逻辑）— 推理链（inference/derivation）

模型定义顺序（避免前向引用）：
  枚举 → AuthorGroup / Topic / Author / MonitoredSource
  → Assumption → Fact → VerificationReference
  → Conclusion / Prediction / Solution
  → Condition → Logic → RawPost
  → FactEvaluation → ConclusionVerdict / PredictionVerdict / SolutionAssessment
  → ImplicitCondition → LogicRelation → PostQualityAssessment → AuthorStanceProfile → AuthorStats
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# 枚举
# ===========================================================================


class FactStatus(str, Enum):
    PENDING = "pending"
    VERIFIED_TRUE = "verified_true"
    VERIFIED_FALSE = "verified_false"
    UNVERIFIABLE = "unverifiable"


class ConclusionStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"


class PredictionStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    UNVERIFIABLE = "unverifiable"
    AWAITING = "awaiting"


class SolutionStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    UNVERIFIABLE = "unverifiable"


class LogicCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    WEAK = "weak"
    INVALID = "invalid"


class SourceType(str, Enum):
    POST = "post"
    PROFILE = "profile"


class EvaluationResult(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


class VerdictResult(str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    PARTIAL = "partial"
    PENDING = "pending"
    EXPIRED = "expired"
    UNVERIFIABLE = "unverifiable"


# ===========================================================================
# 基础实体
# ===========================================================================


class AuthorGroup(SQLModel, table=True):
    """跨平台作者实体 — 将不同平台的同一真实人物关联起来

    由 Layer1 Step A（AuthorGroupMatcher）在创建新 Author 时自动识别并关联。
    """

    __tablename__ = "author_groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_name: str                           # 规范化姓名（如"Ray Dalio"）
    canonical_role: Optional[str] = None          # 规范化职业角色
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Topic(SQLModel, table=True):
    """话题 — 观点聚合的最小主题单元"""

    __tablename__ = "topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    conclusions: List["Conclusion"] = Relationship(back_populates="topic")


class Author(SQLModel, table=True):
    """观点作者 — 跨观点追踪历史准确率"""

    __tablename__ = "authors"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    platform: str
    platform_id: Optional[str] = None
    profile_url: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

    # Layer3 AuthorProfiler 填写：作者角色档案
    role: Optional[str] = None                      # 职业角色，如"对冲基金创始人"、"学术研究员"
    expertise_areas: Optional[str] = None           # 专业领域，如"全球宏观经济、债务周期"
    known_biases: Optional[str] = None              # 已知立场偏见，如"黄金多头倾向"
    credibility_tier: Optional[int] = None          # 1=顶级权威/2=行业专家/3=知名评论员/4=普通媒体/5=未知
    profile_note: Optional[str] = None             # 综合角色描述（LLM 生成，≤80字）
    profile_fetched: bool = False                   # 是否已执行过角色查询
    profile_fetched_at: Optional[datetime] = None   # 最近一次角色查询时间

    # Layer1 Step A 填写：跨平台实体关联（指向 AuthorGroup）
    author_group_id: Optional[int] = Field(default=None, foreign_key="author_groups.id", index=True)

    conclusions: List["Conclusion"] = Relationship(back_populates="author")
    solutions: List["Solution"] = Relationship(back_populates="author")
    monitored_sources: List["MonitoredSource"] = Relationship(back_populates="author")


class MonitoredSource(SQLModel, table=True):
    """监控源 — 持续追踪的 URL（帖子或主页）"""

    __tablename__ = "monitored_sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    source_type: SourceType
    platform: str
    platform_id: str

    author_id: Optional[int] = Field(default=None, foreign_key="authors.id")
    author: Optional[Author] = Relationship(back_populates="monitored_sources")

    is_active: bool = True
    fetch_interval_minutes: int = 60
    last_fetched_at: Optional[datetime] = None
    history_fetched: bool = False

    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 模型零：假设条件（Assumption）
# ===========================================================================


class Assumption(SQLModel, table=True):
    """假设条件

    作者明确陈述的"如果X则..."前提条件。
    Layer2 同步提取（区别于 ImplicitCondition 的未说出前提）。
    """

    __tablename__ = "assumptions"

    id: Optional[int] = Field(default=None, primary_key=True)

    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id")

    condition_text: str                          # ≤60字
    canonical_condition: Optional[str] = None

    verifiable_statement: Optional[str] = None   # 单句可验证陈述
    temporal_type: str = "predictive"            # retrospective | predictive
    temporal_note: Optional[str] = None

    is_verifiable: bool = False

    # 现实对齐（inline）—— Layer3 RealityAligner 填写
    alignment_result: Optional[str] = None       # true|false|uncertain|unavailable
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None         # 1|2|3
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 模型一：事实（Fact）
# ===========================================================================


class Fact(SQLModel, table=True):
    """事实

    Layer2 从帖子中提取的独立可核实陈述。
    与结论/解决方案解耦，通过 Logic 建立论证关系。

    生命周期：
      claim（原文陈述）
        → verifiable_expression（量化可测表达）+ verification_method
        → references（权威数据来源）
        → validity_start / validity_end（验证时效）
        → Layer3 执行验证，更新 status
    """

    __tablename__ = "facts"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 原始陈述（保留原话或紧凑转述）
    claim: str

    # 概念归一化：由 LLM 生成的标准化表达，用于跨帖子概念去重和匹配
    canonical_claim: Optional[str] = None

    # Layer2 生成的单句可验证陈述（供 Layer3 RealityAligner 使用）
    verifiable_statement: Optional[str] = None
    temporal_type: str = "retrospective"             # retrospective | predictive
    temporal_note: Optional[str] = None              # 如 "2025年1月"

    # 转化为可客观核实的具体表达
    verifiable_expression: Optional[str] = None
    is_verifiable: bool = False

    # 验证操作说明：对比哪段时间的哪个数据，判定阈值是什么
    verification_method: Optional[str] = None

    # 验证时效（文本备注 + 解析后的 datetime）
    validity_start_note: Optional[str] = None
    validity_end_note: Optional[str] = None
    validity_start: Optional[datetime] = None
    validity_end: Optional[datetime] = None

    # 验证状态与结果
    status: FactStatus = FactStatus.PENDING
    verified_at: Optional[datetime] = None
    verification_evidence: Optional[str] = None

    # Layer3 填写：核查所用权威信息源
    verified_source_org: Optional[str] = None       # 机构名称，如"国家统计局"
    verified_source_url: Optional[str] = None       # 核查数据的具体 URL
    verified_source_data: Optional[str] = None      # 核查数据的摘要/原文片段

    # 现实对齐（inline）—— Layer3 RealityAligner 填写
    alignment_result: Optional[str] = None          # true|false|uncertain|unavailable
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None            # 1|2|3
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None
    # 宽泛描述判断：none|approximate_ok（不影响核心结论）|approximate_critical（影响核心结论）
    alignment_vagueness: Optional[str] = None

    # 来源帖子（可选，用于溯源）
    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id")

    created_at: datetime = Field(default_factory=_utcnow)

    # 权威数据来源列表
    references: List["VerificationReference"] = Relationship(back_populates="fact")

    # Layer3 验证尝试记录
    evaluations: List["FactEvaluation"] = Relationship(back_populates="fact")


class VerificationReference(SQLModel, table=True):
    """验证引用

    事实验证时所依据的权威数据来源。
    每个事实可以有多条引用。

    原则：
      - organization 必须为已知的权威机构
      - url 若已知则填写具体数据页面地址
      - data_description 说明应查阅的具体数据集或报告名称
    """

    __tablename__ = "verification_references"

    id: Optional[int] = Field(default=None, primary_key=True)
    fact_id: int = Field(foreign_key="facts.id")

    organization: str
    data_description: str
    url: Optional[str] = None
    url_note: Optional[str] = None

    fact: Optional[Fact] = Relationship(back_populates="references")


# ===========================================================================
# 模型二：结论（Conclusion）
# ===========================================================================


class Conclusion(SQLModel, table=True):
    """结论

    作者的分析判断，分两种类型：
      retrospective — 对已发生事件或当前形势的判断（可立即验证）
      predictive    — 对未来事件或趋势的判断（需等待监控期后验证）

    通过 Logic 关联支撑它的事实。
    """

    __tablename__ = "conclusions"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: int = Field(foreign_key="topics.id")
    author_id: int = Field(foreign_key="authors.id")

    claim: str                          # 核心结论陈述（≤80字）
    canonical_claim: Optional[str] = None  # 概念归一化标准形式

    # Layer2 生成的单句可验证陈述
    verifiable_statement: Optional[str] = None
    temporal_type: str = "retrospective"         # retrospective（固定）
    temporal_note: Optional[str] = None

    # 结论类型：回顾型 vs 预测型（保留向后兼容）
    conclusion_type: str = "retrospective"   # retrospective | predictive

    # 结论的有效时效
    time_horizon_note: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    status: ConclusionStatus = ConclusionStatus.PENDING

    # Layer2 提取：作者对该结论的自信程度
    author_confidence: Optional[str] = None       # certain|likely|uncertain|speculative
    author_confidence_note: Optional[str] = None  # 原文中的不确定性表达（如"我认为可能"）

    # Layer3 填写：监控配置（仅 predictive 类型使用）
    monitoring_source_org: Optional[str] = None     # 监控机构，如"中国国家统计局"
    monitoring_source_url: Optional[str] = None     # 监控数据 URL
    monitoring_period_note: Optional[str] = None    # 人读的监控时段说明
    monitoring_start: Optional[datetime] = None     # 监控起点
    monitoring_end: Optional[datetime] = None       # 监控终点

    # Layer3 Step4a：条件型预测分析（仅 predictive 类型）
    # 对"如果X则Y"结构的预测，分析假设条件X的概率后决定如何监控
    conditional_assumption: Optional[str] = None  # 假设条件文本（"如果X"中的X）
    assumption_probability: Optional[str] = None  # high|medium|low|negligible
    # not_applicable=无条件 | abandoned=极低概率放弃验证 | waiting=等待条件触发 | triggered=条件已发生
    conditional_monitoring_status: str = "not_applicable"

    # 现实对齐（inline）—— Layer3 RealityAligner 填写
    alignment_result: Optional[str] = None          # true|false|uncertain|unavailable
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None            # 1|2|3
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None

    # Layer3 逻辑推理：DAG 计算结果
    is_core_conclusion: bool = False    # 没有其他结论以此为前提 → 核心结论
    is_in_cycle: bool = False           # DAG 中存在循环 → 标记为无效

    source_url: str
    source_platform: str
    posted_at: datetime
    collected_at: datetime = Field(default_factory=_utcnow)
    raw_extraction: Optional[str] = None

    topic: Optional[Topic] = Relationship(back_populates="conclusions")
    author: Optional[Author] = Relationship(back_populates="conclusions")
    logics: List["Logic"] = Relationship(back_populates="conclusion")
    verdicts: List["ConclusionVerdict"] = Relationship(back_populates="conclusion")


# ===========================================================================
# 模型三：解决方案（Solution）
# ===========================================================================


class Solution(SQLModel, table=True):
    """解决方案

    作者从结论推导出的具体行动建议（买什么/卖什么/持有什么）。
    通过 Logic（derivation 类型）关联推导所基于的结论。
    Layer3 通过 LLM 模拟执行并配置监控，等待未来验证。
    """

    __tablename__ = "solutions"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id")
    author_id: int = Field(foreign_key="authors.id")

    claim: str                           # 建议内容（≤100字）
    canonical_claim: Optional[str] = None

    # 行动类型与标的
    action_type: Optional[str] = None    # buy/sell/hold/short/diversify/hedge/reduce
    action_target: Optional[str] = None  # 标的物（如"黄金ETF"，"美国10年期国债"）
    action_rationale: Optional[str] = None  # 此建议如何从结论推导

    # Layer3 填写：LLM 模拟执行的描述
    simulated_action_note: Optional[str] = None

    # Layer3 填写：监控配置（同 predictive 结论）
    monitoring_source_org: Optional[str] = None
    monitoring_source_url: Optional[str] = None
    monitoring_period_note: Optional[str] = None
    monitoring_start: Optional[datetime] = None
    monitoring_end: Optional[datetime] = None

    # Layer3 Step4b：发布时刻的标的基准价格/数值
    baseline_value: Optional[str] = None         # 发布时基准价格/数值（如"2680 USD/oz"）
    baseline_metric: Optional[str] = None        # 基准指标说明（如"黄金现货价 USD/oz"）
    baseline_recorded_at: Optional[datetime] = None  # 基准价格记录时间

    status: SolutionStatus = SolutionStatus.PENDING

    source_url: Optional[str] = None
    source_platform: Optional[str] = None
    posted_at: Optional[datetime] = None
    collected_at: datetime = Field(default_factory=_utcnow)
    raw_extraction: Optional[str] = None

    author: Optional[Author] = Relationship(back_populates="solutions")
    assessments: List["SolutionAssessment"] = Relationship(back_populates="solution")


# ===========================================================================
# 模型三点五：预测（Prediction）
# ===========================================================================


class Prediction(SQLModel, table=True):
    """预测

    作者对未来事件或趋势的判断（完全独立于 Conclusion）。
    Layer2 从帖子中提取，Layer3 配置监控并最终验证。
    """

    __tablename__ = "predictions"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id")
    author_id: int = Field(foreign_key="authors.id")

    claim: str                                   # ≤80字原文
    canonical_claim: Optional[str] = None

    verifiable_statement: str = ""               # Layer2 生成的单句可核实表达
    temporal_type: str = "predictive"            # 固定 predictive
    temporal_note: Optional[str] = None          # 如 "2026-2030年"

    author_confidence: Optional[str] = None      # certain|likely|uncertain|speculative
    author_confidence_note: Optional[str] = None

    # 监控字段（从 Conclusion 迁移）
    monitoring_source_org: Optional[str] = None
    monitoring_source_url: Optional[str] = None
    monitoring_period_note: Optional[str] = None
    monitoring_start: Optional[datetime] = None
    monitoring_end: Optional[datetime] = None

    # 条件型预测
    conditional_assumption: Optional[str] = None
    assumption_probability: Optional[str] = None
    conditional_monitoring_status: str = "not_applicable"

    # 现实对齐（inline）—— Layer3 RealityAligner 填写
    alignment_result: Optional[str] = None       # true|false|uncertain|unavailable
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None         # 1|2|3
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None

    status: PredictionStatus = PredictionStatus.PENDING

    source_url: str = ""
    source_platform: str = ""
    posted_at: Optional[datetime] = None
    collected_at: datetime = Field(default_factory=_utcnow)
    raw_extraction: Optional[str] = None

    verdicts: List["PredictionVerdict"] = Relationship(back_populates="prediction")


# ===========================================================================
# 模型五：条件（Condition）— 统一假设条件 + 隐含条件
# ===========================================================================


class Condition(SQLModel, table=True):
    """条件（v3 统一模型）

    将原来的 Assumption（显式假设条件）和 ImplicitCondition（隐含条件）合并为单一模型。

    condition_type:
      assumption — 作者明确陈述的"如果X则Y"条件
      implicit   — 未说出的暗含前提（推理时依赖但未明说）

    对齐验证（alignment_result）：
      assumption → 评估假设条件发生概率（high/medium/low/negligible）
      implicit   → 评估是否为普遍共识（is_consensus=True 则直接标为 true）
    """

    __tablename__ = "conditions"

    id: Optional[int] = Field(default=None, primary_key=True)

    condition_type: str = "assumption"   # assumption | implicit
    condition_text: str                  # 条件陈述（≤120字）
    canonical_condition: Optional[str] = None

    # Layer2 生成
    verifiable_statement: Optional[str] = None
    temporal_note: Optional[str] = None

    # 隐含条件专用：是否为普遍共识
    is_consensus: bool = False
    is_verifiable: bool = False

    # 现实对齐（inline）—— Layer4 RealityAligner 填写
    alignment_result: Optional[str] = None   # true|false|uncertain|unavailable
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None

    raw_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id")
    created_at: datetime = Field(default_factory=_utcnow)


# ===========================================================================
# 模型四：逻辑（Logic）
# ===========================================================================


class Logic(SQLModel, table=True):
    """逻辑

    显式建立推理链，分两种类型：
      inference   — 多种前提→结论的论证关系
                    前提可以是：事实 / 假设性事实 / 隐含条件 / 其他结论
      derivation  — 结论→解决方案的推导关系

    inference 类型字段：
      conclusion_id
      supporting_fact_ids       — 支撑事实（JSON ID 数组）
      assumption_fact_ids       — 假设性事实（待满足的假设前提，JSON ID 数组）
      supporting_conclusion_ids — 作为前提的其他结论（JSON ID 数组，Layer2 填写）
      implicit_condition_ids    — 隐含条件（JSON ID 数组，Layer3 Step1b 填写）

    derivation 类型字段：
      solution_id + source_conclusion_ids（JSON 数组）
    """

    __tablename__ = "logics"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 逻辑类型
    logic_type: str = "inference"        # inference | derivation

    # inference 类型：目标结论
    conclusion_id: Optional[int] = Field(default=None, foreign_key="conclusions.id")

    # derivation 类型：目标解决方案
    solution_id: Optional[int] = Field(default=None, foreign_key="solutions.id")

    # JSON 数组，存储 Fact 的 ID 列表（inference 类型用）
    supporting_fact_ids: str = Field(default="[]")        # 事实：支撑该结论的已知事实
    assumption_fact_ids: str = Field(default="[]")        # 假设性事实：假设条件（待满足）

    # JSON 数组，存储 Conclusion 的 ID 列表（inference 类型：作为前提的其他结论）
    supporting_conclusion_ids: str = Field(default="[]")  # Layer2 填写

    # JSON 数组，存储 ImplicitCondition 的 ID 列表（inference 类型，Layer3 Step1b 填写）
    implicit_condition_ids: str = Field(default="[]")

    # JSON 数组，存储 Conclusion 的 ID 列表（derivation 类型用）
    source_conclusion_ids: Optional[str] = None      # 推导所基于的结论 IDs

    # 新字段 (v2)：预测型逻辑
    prediction_id: Optional[int] = Field(default=None, foreign_key="predictions.id")
    source_prediction_ids: Optional[str] = None      # JSON，derivation 引用 Prediction IDs
    assumption_ids: Optional[str] = None             # JSON，Assumption 表 ID 数组
    layer2_implicit_condition_ids: Optional[str] = None  # JSON，Layer2 生成的 IC ID 数组

    # v3：统一条件 ID 数组（Condition 表）
    condition_ids: Optional[str] = None  # JSON，Condition 表 ID 数组

    # v2：自然语言摘要（程序生成）
    chain_summary: Optional[str] = None              # 自然语言逻辑链摘要
    chain_type: Optional[str] = None                 # inference|prediction|recommendation

    # Layer3 Step1 填写：逻辑合法性验证
    logic_validity: Optional[str] = None             # valid|partial|invalid
    logic_issues: Optional[str] = None               # JSON 问题列表
    logic_verified_at: Optional[datetime] = None

    # Layer3 填写：逻辑完备性评估（旧字段，向后兼容）
    logic_completeness: Optional[LogicCompleteness] = None
    logic_note: Optional[str] = None

    # Layer3 填写：极简一句话总结
    one_sentence_summary: Optional[str] = None
    assessed_at: Optional[datetime] = None          # Layer3 评估时间戳

    created_at: datetime = Field(default_factory=_utcnow)

    conclusion: Optional[Conclusion] = Relationship(back_populates="logics")


# ===========================================================================
# Layer1 原始帖子
# ===========================================================================


class RawPost(SQLModel, table=True):
    """原始帖子 — Layer1 采集的未处理内容"""

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

    # 媒体列表 JSON：[{"type": "photo"|"video"|"gif", "url": "..."}]
    # Layer1 采集时写入；Layer2 提取时由 MediaDescriber 生成描述并追加到 content
    media_json: Optional[str] = None

    is_processed: bool = False
    processed_at: Optional[datetime] = None

    # Layer1 Step B 填写：跨平台内容去重
    is_duplicate: bool = False                        # 是否被判定为跨平台重复内容
    original_post_id: Optional[int] = Field(default=None, foreign_key="raw_posts.id")  # 原始帖子 ID

    monitored_source_id: Optional[int] = Field(
        default=None, foreign_key="monitored_sources.id"
    )


# ===========================================================================
# Layer3：事实验证 & 裁定
# ===========================================================================


class FactEvaluation(SQLModel, table=True):
    """单次事实验证尝试的结果记录"""

    __tablename__ = "fact_evaluations"

    id: Optional[int] = Field(default=None, primary_key=True)
    fact_id: int = Field(foreign_key="facts.id", index=True)

    result: EvaluationResult
    evidence_text: Optional[str] = None    # 支持该结论的证据摘要
    evidence_tier: Optional[int] = None    # 证据分级：1=权威机构/2=金融市场/3=可信第三方
    data_period: Optional[str] = None      # 所依据数据的时间段
    evaluator_notes: Optional[str] = None  # 评估备注

    # Layer3 Step1 填写：多模型投票选出的验证方案（JSON）
    verification_plan_json: Optional[str] = None

    evaluated_at: datetime = Field(default_factory=_utcnow)

    fact: Optional[Fact] = Relationship(back_populates="evaluations")


class ConclusionVerdict(SQLModel, table=True):
    """结论的最终裁定（统一处理 retrospective 和 predictive 两种类型）"""

    __tablename__ = "conclusion_verdicts"

    id: Optional[int] = Field(default=None, primary_key=True)
    conclusion_id: int = Field(foreign_key="conclusions.id", index=True)

    verdict: VerdictResult
    logic_trace: Optional[str] = None  # 推导过程的 JSON 记录

    # Layer3 RoleEvaluator 填写：作者角色匹配度
    role_fit: Optional[str] = None          # appropriate / questionable / mismatched
    role_fit_note: Optional[str] = None     # 角色匹配分析（1句话）

    derived_at: datetime = Field(default_factory=_utcnow)

    conclusion: Optional[Conclusion] = Relationship(back_populates="verdicts")


class PredictionVerdict(SQLModel, table=True):
    """预测的最终裁定（镜像 ConclusionVerdict）"""

    __tablename__ = "prediction_verdicts"

    id: Optional[int] = Field(default=None, primary_key=True)
    prediction_id: int = Field(foreign_key="predictions.id", index=True)

    verdict: VerdictResult
    logic_trace: Optional[str] = None  # 推导过程的 JSON 记录

    # Layer3 RoleEvaluator 填写：作者角色匹配度
    role_fit: Optional[str] = None
    role_fit_note: Optional[str] = None

    derived_at: datetime = Field(default_factory=_utcnow)

    prediction: Optional[Prediction] = Relationship(back_populates="verdicts")


class SolutionAssessment(SQLModel, table=True):
    """解决方案的评估记录"""

    __tablename__ = "solution_assessments"

    id: Optional[int] = Field(default=None, primary_key=True)
    solution_id: int = Field(foreign_key="solutions.id", index=True)

    verdict: VerdictResult
    evidence_text: Optional[str] = None
    evidence_tier: Optional[int] = None   # 1/2/3
    assessed_at: datetime = Field(default_factory=_utcnow)
    notes: Optional[str] = None

    # Layer3 RoleEvaluator 填写：作者角色匹配度
    role_fit: Optional[str] = None          # appropriate / questionable / mismatched
    role_fit_note: Optional[str] = None     # 角色匹配分析（1句话）

    solution: Optional[Solution] = Relationship(back_populates="assessments")


class ImplicitCondition(SQLModel, table=True):
    """隐含条件

    从 Fact 或 Conclusion 中识别出的未明说前提假设。
    通过多次 LLM 投票判断该条件是否成立（consensus/true/false/uncertain）。

    由 Layer3 Step 1b（ImplicitConditionExtractor）填写。
    """

    __tablename__ = "implicit_conditions"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 关联到 Fact 或 Conclusion 或 Prediction（之一）
    fact_id: Optional[int] = Field(default=None, foreign_key="facts.id", index=True)
    conclusion_id: Optional[int] = Field(default=None, foreign_key="conclusions.id", index=True)
    prediction_id: Optional[int] = Field(default=None, foreign_key="predictions.id", index=True)

    condition_text: str            # 隐含条件陈述（≤80字）

    # v2 新增：可验证陈述与时态
    verifiable_statement: Optional[str] = None
    temporal_type: Optional[str] = None
    temporal_note: Optional[str] = None

    # 判定结果：pending | consensus（普遍共识）| not_consensus（非共识/有争议）
    verification_result: str = "pending"
    verification_note: Optional[str] = None   # 判断依据说明（≤100字）

    # LLM 3次投票统计（投"是共识"/"非共识"的票数）
    vote_consensus: int = 0
    vote_not_consensus: int = 0

    # Phase C — 近年共识趋势
    # strengthening（增强）| weakening（松动）| stable（稳定）| unknown（不确定）
    consensus_trend: Optional[str] = None
    consensus_trend_note: Optional[str] = None  # 趋势依据（≤100字）

    # 现实对齐（inline）—— Layer3 RealityAligner 填写
    alignment_result: Optional[str] = None
    alignment_evidence: Optional[str] = None
    alignment_tier: Optional[int] = None
    alignment_confidence: Optional[str] = None
    alignment_verified_at: Optional[datetime] = None

    is_consensus: bool = False

    created_at: datetime = Field(default_factory=_utcnow)


class LogicRelation(SQLModel, table=True):
    """逻辑间关系

    记录同一篇文章中，某条逻辑的结论或论证内容是否构成另一条逻辑的前提或背景。
    由 Layer3 Step 5（LogicRelationMapper）填写。

    from_logic_id → to_logic_id 表示 "from 支撑/支持/背景化 to"

    relation_type:
      supports       — from 的结论是 to 的直接逻辑前提
      contextualizes — from 为 to 提供了论证所需的背景框架
      contradicts    — from 与 to 的前提或结论存在直接矛盾
    """

    __tablename__ = "logic_relations"

    id: Optional[int] = Field(default=None, primary_key=True)
    from_logic_id: int = Field(foreign_key="logics.id", index=True)
    to_logic_id: int = Field(foreign_key="logics.id", index=True)
    relation_type: str   # supports | contextualizes | contradicts
    note: Optional[str] = None   # 一句话说明：from 的哪个论点构成了 to 的什么前提

    created_at: datetime = Field(default_factory=_utcnow)


class PostQualityAssessment(SQLModel, table=True):
    """单篇内容质量评估

    记录每篇内容的独特性和有效性评估结果。
    由 Layer3 Step 8（PostQualityEvaluator）填写。

    独特性（uniqueness）：
      - 数据库中有多少作者表达了相似观点（canonical_claim 语义匹配）
      - 当前作者是否是第一个提出此类观点的（first_mover）

    有效性（effectiveness）：
      - 内容中实质性内容与噪声的比率
      - 噪声：情绪性表达、娱乐性插话、无实质内容的废话
    """

    __tablename__ = "post_quality_assessments"

    id: Optional[int] = Field(default=None, primary_key=True)
    raw_post_id: int = Field(foreign_key="raw_posts.id", unique=True, index=True)
    author_id: int = Field(foreign_key="authors.id", index=True)

    # ── 内容独特性 ──────────────────────────────────────────────────────────
    uniqueness_score: Optional[float] = None     # 0.0-1.0，越高越独特
    uniqueness_note: Optional[str] = None        # 独特性分析说明
    is_first_mover: Optional[bool] = None        # 是否是第一个表达此类观点的
    similar_claim_count: int = 0                 # 数据库中相似观点的数量（不含自身）
    similar_author_count: int = 0                # 数据库中表达相似观点的其他作者数量

    # ── 内容有效性 ──────────────────────────────────────────────────────────
    effectiveness_score: Optional[float] = None  # 0.0-1.0，越高越有效
    effectiveness_note: Optional[str] = None     # 有效性分析说明
    noise_ratio: Optional[float] = None          # 0.0-1.0，噪声比例（0=无噪声）
    # 噪声类型（可多选，JSON array of strings）：
    # "emotional_rhetoric"（情绪主导）、"entertainment"（娱乐性插话）、"filler"（废话）
    noise_types: Optional[str] = None           # JSON array

    # ── 文章立场分析（Layer3 Step8 填写）──────────────────────────────────────
    # 看涨/多头 | 看跌/空头 | 中立/客观 | 警告/防御 | 批判/质疑 | 政策倡导 | 教育/分析 | 其他
    stance_label: Optional[str] = None
    stance_note: Optional[str] = None           # 立场说明（≤80字）

    assessed_at: datetime = Field(default_factory=_utcnow)


class AuthorStanceProfile(SQLModel, table=True):
    """作者立场分布档案

    汇总该作者所有已分析内容的立场分布（stance_label），形成历史立场档案。
    由 Layer3 Step 9b（AuthorStanceUpdater）在每次新内容处理后更新。

    立场类别（与 PostQualityAssessment.stance_label 对齐）：
      看涨/多头 | 看跌/空头 | 中立/客观 | 警告/防御 | 批判/质疑 | 政策倡导 | 教育/分析 | 其他
    """

    __tablename__ = "author_stance_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int = Field(foreign_key="authors.id", unique=True, index=True)

    # JSON dict: {"看涨/多头": 5, "看跌/空头": 2, "中立/客观": 3, ...}
    stance_distribution: Optional[str] = None
    dominant_stance: Optional[str] = None          # 出现次数最多的立场标签
    dominant_stance_ratio: Optional[float] = None  # 主导立场占比 (0.0-1.0)
    total_analyzed: int = 0                        # 已纳入统计的帖子数（有 stance_label 的）

    last_updated: datetime = Field(default_factory=_utcnow)


class AuthorStats(SQLModel, table=True):
    """作者综合评估统计

    长期追踪每位作者在 7 个维度上的表现，形成量化的可信度档案。
    由 Layer3 Step 9（AuthorStatsUpdater）在每次新内容处理后更新。

    7 个评估维度：
      1. fact_accuracy_rate      — 事实准确率（FactEvaluations 中 true 的比率）
      2. conclusion_accuracy_rate — 结论准确性（ConclusionVerdicts 中 confirmed 的比率）
      3. prediction_accuracy_rate — 预测准确性（predictive 结论中 confirmed 的比率）
      4. logic_rigor_score        — 逻辑严谨性（LogicEvaluation completeness 评分）
      5. recommendation_reliability_rate — 建议可靠性（SolutionAssessments 中 validated 的比率）
      6. content_uniqueness_score  — 内容独特性（PostQualityAssessment.uniqueness_score 均值）
      7. content_effectiveness_score — 内容有效性（PostQualityAssessment.effectiveness_score 均值）
    """

    __tablename__ = "author_stats"

    id: Optional[int] = Field(default=None, primary_key=True)
    author_id: int = Field(foreign_key="authors.id", unique=True, index=True)

    # ── 事实准确率 ──────────────────────────────────────────────────────────
    fact_accuracy_rate: Optional[float] = None   # 0.0-1.0
    fact_accuracy_sample: int = 0                # 已评估事实数量

    # ── 结论准确性 ──────────────────────────────────────────────────────────
    conclusion_accuracy_rate: Optional[float] = None
    conclusion_accuracy_sample: int = 0

    # ── 预测准确性（仅 predictive 类型）────────────────────────────────────
    prediction_accuracy_rate: Optional[float] = None
    prediction_accuracy_sample: int = 0

    # ── 逻辑严谨性 ──────────────────────────────────────────────────────────
    logic_rigor_score: Optional[float] = None    # 0.0-1.0
    logic_rigor_sample: int = 0

    # ── 建议可靠性 ──────────────────────────────────────────────────────────
    recommendation_reliability_rate: Optional[float] = None
    recommendation_reliability_sample: int = 0

    # ── 内容独特性 ──────────────────────────────────────────────────────────
    content_uniqueness_score: Optional[float] = None
    content_uniqueness_sample: int = 0

    # ── 内容有效性 ──────────────────────────────────────────────────────────
    content_effectiveness_score: Optional[float] = None
    content_effectiveness_sample: int = 0

    # ── 综合评分 ──────────────────────────────────────────────────────────
    # 加权综合：事实*20% + 结论*15% + 预测*20% + 逻辑*15% + 建议*15% + 独特*7.5% + 有效*7.5%
    overall_credibility_score: Optional[float] = None  # 0.0-100.0

    total_posts_analyzed: int = 0
    last_updated: datetime = Field(default_factory=_utcnow)
