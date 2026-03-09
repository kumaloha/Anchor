"""
Layer 2 提取 Schema（v4 — 六实体体系）
========================================
六实体：Fact / Assumption / ImplicitCondition / Conclusion / Prediction / Solution
+ 显式关系边（ExtractedRelationship）

关键区分规则：
  Conclusion = 对过去/当前状态的判断（回顾型）
  Prediction = 指向未来，必须提取 temporal_note（无则标 no_timeframe）
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """Layer2 提取的事实依据"""
    summary: Optional[str] = None            # 一句话摘要（≤15字，高度抽象，用于展示）
    claim: str                                # 原文陈述（≤120字）
    verifiable_statement: str                 # 单句可核实陈述（供 Chain3 使用）
    temporal_type: Literal["retrospective", "predictive"] = "retrospective"
    temporal_note: Optional[str] = None       # 时间范围标注


class ExtractedAssumption(BaseModel):
    """Layer2 提取的假设条件（作者明确陈述的 if-X-then-Y 前提）"""
    summary: Optional[str] = None            # 一句话摘要（≤15字）
    condition_text: str                       # 假设条件陈述（≤120字）
    verifiable_statement: Optional[str] = None
    temporal_note: Optional[str] = None


class ExtractedImplicitCondition(BaseModel):
    """Layer2 提取的隐含条件（推理中未说出的暗含前提）"""
    summary: Optional[str] = None            # 一句话摘要（≤15字）
    condition_text: str                       # 隐含条件陈述（≤120字）
    is_obvious_consensus: bool = False        # 显而易见的共识（Chain3 可跳过 LLM）


class ExtractedConclusion(BaseModel):
    """Layer2 提取的结论（回顾型 — 对过去/当前状态的判断）"""
    summary: Optional[str] = None            # 一句话摘要（≤15字）
    claim: str                                # 结论陈述（≤120字）
    verifiable_statement: str                 # 单句可核实陈述
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedPrediction(BaseModel):
    """Layer2 提取的预测（指向未来）"""
    summary: Optional[str] = None            # 一句话摘要（≤15字）
    claim: str                                # 预测陈述（≤120字）
    temporal_note: Optional[str] = None       # 时间范围（无则 None，Chain1 标注 no_timeframe）
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedSolution(BaseModel):
    """Layer2 提取的解决方案（作者建议的行动）"""
    summary: Optional[str] = None            # 一句话摘要（≤15字）
    claim: str                                # 建议内容（≤120字）
    action_type: Optional[str] = None        # buy|sell|hold|short|diversify|hedge|reduce|advocate
    action_target: Optional[str] = None      # 行动标的
    action_rationale: Optional[str] = None   # 推导依据


class ExtractedRelationship(BaseModel):
    """Layer2 提取的关系边

    使用数组下标（index）引用实体，Chain1 写库后转换为 DB ID。

    edge_type 枚举：
      fact_supports_conclusion
      assumption_conditions_conclusion
      implicit_conditions_conclusion
      conclusion_supports_conclusion
      conclusion_leads_to_prediction
      conclusion_enables_solution
    """
    source_type: str           # fact|assumption|implicit_condition|conclusion|prediction|solution
    source_index: int          # 对应实体数组中的下标
    target_type: str
    target_index: int
    edge_type: str             # EdgeType 枚举值
    note: Optional[str] = None


class ExtractionResult(BaseModel):
    """Layer2 完整提取结果（六实体 + 关系边）"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    extraction_notes: Optional[str] = None
    article_summary: Optional[str] = None   # Step5 叙事摘要

    facts: List[ExtractedFact] = Field(default_factory=list)
    assumptions: List[ExtractedAssumption] = Field(default_factory=list)
    implicit_conditions: List[ExtractedImplicitCondition] = Field(default_factory=list)
    conclusions: List[ExtractedConclusion] = Field(default_factory=list)
    predictions: List[ExtractedPrediction] = Field(default_factory=list)
    solutions: List[ExtractedSolution] = Field(default_factory=list)
    relationships: List[ExtractedRelationship] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# v5 多步流水线中间 Schema
# ---------------------------------------------------------------------------


class RawClaim(BaseModel):
    """Step 1 输出：未分类的原始声明节点"""
    id: int
    text: str       # ≤120字，作者的原文声明
    summary: str    # ≤15字摘要


class RawEdge(BaseModel):
    """Step 1 输出：声明间有向边（前提 → 结论方向）"""
    from_id: int    # 前提节点 id
    to_id: int      # 结论节点 id


class Step1Result(BaseModel):
    """Step 1 完整输出"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    claims: List[RawClaim] = Field(default_factory=list)
    edges: List[RawEdge] = Field(default_factory=list)


class MergeGroup(BaseModel):
    """Step 2 输出：同义节点合并组"""
    keep: int                        # 保留的 claim id（作为合并后节点的 id）
    discard: List[int]               # 废弃的 claim ids
    merged_text: str                 # 合并后新文本（≤120字，综合所有被合并节点的内容）
    merged_summary: str              # 合并后摘要（≤15字）


class Step2Result(BaseModel):
    """Step 2 完整输出"""
    merges: List[MergeGroup] = Field(default_factory=list)


class ClassifiedEntity(BaseModel):
    """Step 3 输出：单个节点的分类结果"""
    claim_id: int
    entity_type: str    # fact|conclusion|prediction|assumption|solution
    author_confidence: Optional[str] = None       # for conclusion/prediction: certain|likely|uncertain|speculative
    temporal_note: Optional[str] = None           # for prediction
    verifiable_statement: Optional[str] = None    # for fact/conclusion
    action_type: Optional[str] = None             # for solution
    action_target: Optional[str] = None           # for solution
    action_rationale: Optional[str] = None        # for solution


class Step3Result(BaseModel):
    """Step 3 完整输出"""
    classifications: List[ClassifiedEntity] = Field(default_factory=list)


class ImplicitConditionItem(BaseModel):
    """Step 4 输出：单个隐含条件"""
    summary: str
    condition_text: str
    target_claim_id: int        # 指向哪个节点（有入边的节点）
    is_obvious_consensus: bool = False


class Step4Result(BaseModel):
    """Step 4 完整输出"""
    implicit_conditions: List[ImplicitConditionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy 模式专用 Schema（content_mode="policy"）
# ---------------------------------------------------------------------------


class PolicyItem(BaseModel):
    """政策条目（Step1PolicyResult 的子项）"""
    summary: str                                     # ≤15字摘要
    policy_text: str                                 # 政策内容（≤120字）
    urgency: str                                     # mandatory|encouraged|pilot|gradual
    metric_value: Optional[str] = None              # 量化值（如 "4%", "1.3万亿"）
    target_year: Optional[str] = None               # 目标年份（如 "2026"）
    is_hard_target: bool = False                     # 是否量化硬约束


class PolicyThemeItem(BaseModel):
    """政策主旨（含所属政策列表）"""
    theme_name: str                                  # 民生 / 产业 / 军事 / 财政 ...
    background: Optional[str] = None                # 背景与目的（≤200字，Chain 1 从文件推断）
    enforcement_note: Optional[str] = None          # 组织保障描述（≤80字）
    has_enforcement_teeth: bool = False              # 是否纳入考核/有执行主体
    policies: List[PolicyItem] = Field(default_factory=list)


class Step1PolicyResult(BaseModel):
    """Policy 模式 Step1 完整输出"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    # issuing_authority / authority_level 由 Chain 2 识别，Chain 1 不提取
    themes: List[PolicyThemeItem] = Field(default_factory=list)
    # 变化标注事实（[删除] 类表述保留为 Fact）
    facts: List[RawClaim] = Field(default_factory=list)
    # 总体政策方向结论
    conclusions: List[RawClaim] = Field(default_factory=list)


class PolicyChangeAnnotation(BaseModel):
    """比对步骤输出：单条政策的变化标注"""
    policy_id: int                                   # 当年 PolicyItem DB id
    change_type: str                                 # 新增|调整|延续
    change_note: Optional[str] = None               # ≤30字说明变化点


class PolicyComparisonResult(BaseModel):
    """比对步骤完整输出"""
    annotations: List[PolicyChangeAnnotation] = Field(default_factory=list)
    deleted_summaries: List[str] = Field(default_factory=list)  # 上年有、今年删除的政策摘要


# ---------------------------------------------------------------------------
# Policy v3 — 六维属性 + 手段子条目 Schema
# ---------------------------------------------------------------------------


class PolicyMeasureSchema(BaseModel):
    """手段子条目（每条具体措施一条）"""
    summary: str                                     # ≤15字摘要
    measure_text: str                                # 具体措施 ≤150字
    trend: str                                       # 升级|降级|延续|新增|删除
    trend_note: Optional[str] = None                 # 变化说明 ≤30字


class PolicySchema(BaseModel):
    """政策主旨（六维属性 + 手段列表）"""
    theme: str                                       # 主旨 ≤8字
    change_summary: Optional[str] = None             # 一句话变化总结 ≤50字
    target: Optional[str] = None                    # 当年目标
    target_prev: Optional[str] = None               # 上年目标
    intensity: str                                   # strong|moderate|weak
    intensity_prev: Optional[str] = None             # 上年 intensity
    intensity_note: Optional[str] = None             # 当年力度说明 ≤60字
    intensity_note_prev: Optional[str] = None        # 上年力度说明 ≤60字
    background: Optional[str] = None                # 当年背景
    background_prev: Optional[str] = None           # 上年背景
    organization: Optional[str] = None              # 当年组织保障
    organization_prev: Optional[str] = None         # 上年组织保障
    measures: List[PolicyMeasureSchema] = Field(default_factory=list)


class PolicyExtractionResult(BaseModel):
    """Policy v3 完整提取结果"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    policies: List[PolicySchema] = Field(default_factory=list)
    facts: List[RawClaim] = Field(default_factory=list)
    conclusions: List[RawClaim] = Field(default_factory=list)
