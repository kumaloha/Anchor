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
    claim: str                                # 原文陈述（≤120字）
    verifiable_statement: str                 # 单句可核实陈述（供 Chain3 使用）
    temporal_type: Literal["retrospective", "predictive"] = "retrospective"
    temporal_note: Optional[str] = None       # 时间范围标注


class ExtractedAssumption(BaseModel):
    """Layer2 提取的假设条件（作者明确陈述的 if-X-then-Y 前提）"""
    condition_text: str                       # 假设条件陈述（≤120字）
    verifiable_statement: Optional[str] = None
    temporal_note: Optional[str] = None


class ExtractedImplicitCondition(BaseModel):
    """Layer2 提取的隐含条件（推理中未说出的暗含前提）"""
    condition_text: str                       # 隐含条件陈述（≤120字）
    is_obvious_consensus: bool = False        # 显而易见的共识（Chain3 可跳过 LLM）


class ExtractedConclusion(BaseModel):
    """Layer2 提取的结论（回顾型 — 对过去/当前状态的判断）"""
    claim: str                                # 结论陈述（≤120字）
    verifiable_statement: str                 # 单句可核实陈述
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedPrediction(BaseModel):
    """Layer2 提取的预测（指向未来）"""
    claim: str                                # 预测陈述（≤120字）
    temporal_note: Optional[str] = None       # 时间范围（无则 None，Chain1 标注 no_timeframe）
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None


class ExtractedSolution(BaseModel):
    """Layer2 提取的解决方案（作者建议的行动）"""
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

    facts: List[ExtractedFact] = Field(default_factory=list)
    assumptions: List[ExtractedAssumption] = Field(default_factory=list)
    implicit_conditions: List[ExtractedImplicitCondition] = Field(default_factory=list)
    conclusions: List[ExtractedConclusion] = Field(default_factory=list)
    predictions: List[ExtractedPrediction] = Field(default_factory=list)
    solutions: List[ExtractedSolution] = Field(default_factory=list)
    relationships: List[ExtractedRelationship] = Field(default_factory=list)
