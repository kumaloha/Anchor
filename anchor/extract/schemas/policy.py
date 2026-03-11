"""
Policy 模式专用 Schema
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from anchor.extract.schemas.v5 import RawClaim


class PolicyItem(BaseModel):
    """政策条目"""
    summary: str
    policy_text: str
    urgency: str
    metric_value: Optional[str] = None
    target_year: Optional[str] = None
    is_hard_target: bool = False


class PolicyThemeItem(BaseModel):
    """政策主旨（含所属政策列表）"""
    theme_name: str
    background: Optional[str] = None
    enforcement_note: Optional[str] = None
    has_enforcement_teeth: bool = False
    policies: List[PolicyItem] = Field(default_factory=list)


class Step1PolicyResult(BaseModel):
    """Policy 模式 Step1 完整输出"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    themes: List[PolicyThemeItem] = Field(default_factory=list)
    facts: List[RawClaim] = Field(default_factory=list)
    conclusions: List[RawClaim] = Field(default_factory=list)


class PolicyChangeAnnotation(BaseModel):
    """比对步骤输出：单条政策的变化标注"""
    policy_id: int
    change_type: str
    change_note: Optional[str] = None


class PolicyComparisonResult(BaseModel):
    """比对步骤完整输出"""
    annotations: List[PolicyChangeAnnotation] = Field(default_factory=list)
    deleted_summaries: List[str] = Field(default_factory=list)


class PolicyMeasureSchema(BaseModel):
    """手段子条目"""
    summary: str
    measure_text: str
    trend: str
    trend_note: Optional[str] = None


class PolicySchema(BaseModel):
    """政策主旨（六维属性 + 手段列表）"""
    theme: str
    change_summary: Optional[str] = None
    target: Optional[str] = None
    target_prev: Optional[str] = None
    intensity: str
    intensity_prev: Optional[str] = None
    intensity_note: Optional[str] = None
    intensity_note_prev: Optional[str] = None
    background: Optional[str] = None
    background_prev: Optional[str] = None
    organization: Optional[str] = None
    organization_prev: Optional[str] = None
    measures: List[PolicyMeasureSchema] = Field(default_factory=list)


class PolicyExtractionResult(BaseModel):
    """Policy v3 完整提取结果"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    policies: List[PolicySchema] = Field(default_factory=list)
    facts: List[RawClaim] = Field(default_factory=list)
    conclusions: List[RawClaim] = Field(default_factory=list)
