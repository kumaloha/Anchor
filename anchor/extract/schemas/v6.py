"""
v6 Top-Down Extraction Pipeline Schema
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class CoreConclusion(BaseModel):
    """v6 Step 1: 核心结论"""
    id: int
    claim: str
    summary: str
    author_confidence: Optional[str] = None
    verifiable_statement: str


class KeyTheory(BaseModel):
    """v6 Step 1: 关键理论"""
    id: int
    claim: str
    summary: str


class TopDownAnchorsResult(BaseModel):
    """v6 Call 1 输出：核心结论 + 关键理论"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    core_conclusions: List[CoreConclusion] = Field(default_factory=list)
    key_theories: List[KeyTheory] = Field(default_factory=list)


class SupportingFact(BaseModel):
    """v6 Step 2: 支撑事实"""
    id: int
    claim: str
    summary: str
    verifiable_statement: str
    temporal_type: str = "retrospective"
    temporal_note: Optional[str] = None


class SubConclusion(BaseModel):
    """v6 Step 2: 子结论"""
    id: int
    claim: str
    summary: str
    verifiable_statement: str
    author_confidence: Optional[str] = None


class SupportingAssumption(BaseModel):
    """v6 Step 2: 支撑假设"""
    id: int
    condition_text: str
    summary: str
    verifiable_statement: Optional[str] = None


class SupportingPrediction(BaseModel):
    """v6 Step 2: 支撑预测"""
    id: int
    claim: str
    summary: str
    temporal_note: Optional[str] = None
    author_confidence: Optional[str] = None


class SupportingSolution(BaseModel):
    """v6 Step 2: 支撑方案"""
    id: int
    claim: str
    summary: str
    action_type: Optional[str] = None
    action_target: Optional[str] = None
    action_rationale: Optional[str] = None


class SupportingScanResult(BaseModel):
    """v6 Call 2 输出：相关支撑实体"""
    facts: List[SupportingFact] = Field(default_factory=list)
    sub_conclusions: List[SubConclusion] = Field(default_factory=list)
    assumptions: List[SupportingAssumption] = Field(default_factory=list)
    predictions: List[SupportingPrediction] = Field(default_factory=list)
    solutions: List[SupportingSolution] = Field(default_factory=list)


class TypedEntity(BaseModel):
    """v6 统一实体格式（Call 3-5 共用）"""
    id: int
    entity_type: str  # fact|conclusion|theory|prediction|solution|assumption
    claim: str = ""
    summary: str = ""
    is_core: bool = False
    verifiable_statement: Optional[str] = None
    author_confidence: Optional[str] = None
    temporal_type: Optional[str] = None
    temporal_note: Optional[str] = None
    action_type: Optional[str] = None
    action_target: Optional[str] = None
    action_rationale: Optional[str] = None
    condition_text: Optional[str] = None

    @field_validator("claim", "summary", mode="before")
    @classmethod
    def coerce_null_to_str(cls, v):
        """LLM 偶尔返回 null，转为空字符串避免 Pydantic 拒绝整批实体。"""
        return v if isinstance(v, str) else ""


class AbstractedResult(BaseModel):
    """v6 Call 3 输出：精炼后的实体列表"""
    entities: List[TypedEntity] = Field(default_factory=list)


class MergeDecision(BaseModel):
    """v6 Call 4 单条合并指令"""
    keep_id: int
    remove_id: int
    merged_claim: str = ""
    merged_summary: str = ""
    reason: str = ""


class MergedResult(BaseModel):
    """v6 Call 4 输出：合并指令列表"""
    merges: List[MergeDecision] = Field(default_factory=list)


class TypedEdge(BaseModel):
    """v6 Call 5 输出：有向边"""
    source_id: int
    target_id: int
    edge_type: str


class RelationshipResult(BaseModel):
    """v6 Call 5 输出：关系边列表"""
    edges: List[TypedEdge] = Field(default_factory=list)
