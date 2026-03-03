"""
Layer 2 提取 Schema（v3 — 四实体体系）
========================================
四实体：Fact / Conclusion / Condition / Solution
  - Conclusion 覆盖回顾型 + 预测型（conclusion_type 区分）
  - Condition 统一假设条件（assumption）+ 隐含条件（implicit）
  - Logic 只有 inference 和 derivation 两种类型
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """Layer2 提取的事实依据"""
    claim: str                          # 原文表达（≤120字）
    canonical_claim: Optional[str] = None
    verifiable_statement: str           # 单句可验证陈述（供 Layer4 使用）
    temporal_type: Literal["retrospective", "predictive"] = "retrospective"
    temporal_note: Optional[str] = None  # 时间范围标注
    verifiable_expression: Optional[str] = None
    is_verifiable: bool = True
    verification_method: Optional[str] = None


class ExtractedConclusion(BaseModel):
    """Layer2 提取的结论（回顾型 + 预测型）"""
    topic: str
    claim: str                          # 核心结论陈述（≤120字）
    canonical_claim: Optional[str] = None
    verifiable_statement: str           # 单句可验证陈述
    conclusion_type: Literal["retrospective", "predictive"] = "retrospective"
    temporal_note: Optional[str] = None  # 预测型必填：时间范围
    author_confidence: Optional[Literal["certain", "likely", "uncertain", "speculative"]] = None
    author_confidence_note: Optional[str] = None


class ExtractedCondition(BaseModel):
    """Layer2 提取的条件（统一假设条件 + 隐含条件）"""
    condition_text: str                 # 条件陈述（≤120字）
    condition_type: Literal["assumption", "implicit"]
    # assumption = 作者明确表述的"如果X则Y"条件
    # implicit   = 推理中未说出的暗含前提
    verifiable_statement: Optional[str] = None
    temporal_note: Optional[str] = None
    is_consensus: bool = False          # 隐含条件专用：是否为普遍共识
    is_verifiable: bool = False


class ExtractedSolution(BaseModel):
    """Layer2 提取的解决方案（作者建议的行动）"""
    topic: str
    claim: str
    canonical_claim: Optional[str] = None
    action_type: Optional[str] = None   # buy/sell/hold/avoid/advocate 等
    action_target: Optional[str] = None # 行动对象（资产、政策、机构等）
    action_rationale: Optional[str] = None


class ExtractedLogic(BaseModel):
    """Layer2 提取的逻辑边（v3 简化版）

    logic_type:
      inference   — 事实/条件/子结论 → 结论
      derivation  — 结论 → 解决方案

    inference 字段：
      target_conclusion_index: 目标结论在 conclusions[] 中的下标
      supporting_fact_indices: 支撑事实下标列表
      supporting_condition_indices: 支撑条件下标列表（新，替代旧的 assumption_fact_indices）
      supporting_conclusion_indices: 作为前提的子结论下标列表

    derivation 字段：
      solution_index: 目标解决方案下标
      source_conclusion_indices: 源结论下标列表
    """
    logic_type: Literal["inference", "derivation"]

    # inference 目标
    target_conclusion_index: Optional[int] = None

    # inference 前提
    supporting_fact_indices: List[int] = Field(default_factory=list)
    supporting_condition_indices: List[int] = Field(default_factory=list)
    supporting_conclusion_indices: List[int] = Field(default_factory=list)

    # derivation 目标
    solution_index: Optional[int] = None

    # derivation 源
    source_conclusion_indices: List[int] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    """Layer2 完整提取结果（四实体）"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    extraction_notes: Optional[str] = None

    facts: List[ExtractedFact] = Field(default_factory=list)
    conclusions: List[ExtractedConclusion] = Field(default_factory=list)
    conditions: List[ExtractedCondition] = Field(default_factory=list)
    solutions: List[ExtractedSolution] = Field(default_factory=list)
    logics: List[ExtractedLogic] = Field(default_factory=list)
