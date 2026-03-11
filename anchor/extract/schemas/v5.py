"""
v5 多步流水线 Schema（七实体 + 关系边）
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """Layer2 提取的事实依据"""
    summary: Optional[str] = None
    claim: str
    verifiable_statement: str
    temporal_type: Literal["retrospective", "predictive"] = "retrospective"
    temporal_note: Optional[str] = None


class ExtractedAssumption(BaseModel):
    """Layer2 提取的假设条件"""
    summary: Optional[str] = None
    condition_text: str
    verifiable_statement: Optional[str] = None
    temporal_note: Optional[str] = None


class ExtractedImplicitCondition(BaseModel):
    """Layer2 提取的隐含条件"""
    summary: Optional[str] = None
    condition_text: str
    is_obvious_consensus: bool = False


class ExtractedConclusion(BaseModel):
    """Layer2 提取的结论"""
    summary: Optional[str] = None
    claim: str
    verifiable_statement: str
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedPrediction(BaseModel):
    """Layer2 提取的预测"""
    summary: Optional[str] = None
    claim: str
    temporal_note: Optional[str] = None
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedSolution(BaseModel):
    """Layer2 提取的解决方案"""
    summary: Optional[str] = None
    claim: str
    action_type: Optional[str] = None
    action_target: Optional[str] = None
    action_rationale: Optional[str] = None


class ExtractedTheory(BaseModel):
    """Layer2 提取的理论框架"""
    summary: Optional[str] = None
    claim: str


class ExtractedRelationship(BaseModel):
    """Layer2 提取的关系边"""
    source_type: str
    source_index: int
    target_type: str
    target_index: int
    edge_type: str
    note: Optional[str] = None


class ExtractionResult(BaseModel):
    """Layer2 完整提取结果（七实体 + 关系边）"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    extraction_notes: Optional[str] = None
    article_summary: Optional[str] = None

    facts: List[ExtractedFact] = Field(default_factory=list)
    assumptions: List[ExtractedAssumption] = Field(default_factory=list)
    implicit_conditions: List[ExtractedImplicitCondition] = Field(default_factory=list)
    conclusions: List[ExtractedConclusion] = Field(default_factory=list)
    predictions: List[ExtractedPrediction] = Field(default_factory=list)
    solutions: List[ExtractedSolution] = Field(default_factory=list)
    theories: List[ExtractedTheory] = Field(default_factory=list)
    relationships: List[ExtractedRelationship] = Field(default_factory=list)


# v5 多步流水线中间 Schema

class RawClaim(BaseModel):
    """Step 1 输出：未分类的原始声明节点"""
    id: int
    text: str
    summary: str


class RawEdge(BaseModel):
    """Step 1 输出：声明间有向边"""
    from_id: int
    to_id: int


class Step1Result(BaseModel):
    """Step 1 完整输出"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    claims: List[RawClaim] = Field(default_factory=list)
    edges: List[RawEdge] = Field(default_factory=list)


class MergeGroup(BaseModel):
    """Step 2 输出：同义节点合并组"""
    keep: int
    discard: List[int]
    merged_text: str
    merged_summary: str


class Step2Result(BaseModel):
    """Step 2 完整输出"""
    merges: List[MergeGroup] = Field(default_factory=list)


class ClassifiedEntity(BaseModel):
    """Step 3 输出：单个节点的分类结果"""
    claim_id: int
    entity_type: str
    author_confidence: Optional[str] = None
    temporal_note: Optional[str] = None
    verifiable_statement: Optional[str] = None
    action_type: Optional[str] = None
    action_target: Optional[str] = None
    action_rationale: Optional[str] = None


class Step3Result(BaseModel):
    """Step 3 完整输出"""
    classifications: List[ClassifiedEntity] = Field(default_factory=list)


class ImplicitConditionItem(BaseModel):
    """Step 4 输出：单个隐含条件"""
    summary: str
    condition_text: str
    target_claim_id: int
    is_obvious_consensus: bool = False


class Step4Result(BaseModel):
    """Step 4 完整输出"""
    implicit_conditions: List[ImplicitConditionItem] = Field(default_factory=list)
