"""
Industry extraction pipeline Pydantic schemas.

Call 1: IndustryContextResult — industry context + players + supply nodes
Call 2: IndustryEntitiesResult — issues + tech routes + metrics
Call 3: IndustryRelationshipResult — cross-layer edges
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Call 1 输出 ──────────────────────────────────────────────────────────────


class ExtractedPlayer(BaseModel):
    """LLM 提取的产业玩家"""
    temp_id: str                             # 临时 ID（如 "p0", "p1"），供 Call 2/3 引用
    name: str                                # 原文中出现的名称
    canonical_name: str                      # 归一化名称（英文公司名或中文通用名）
    aliases: List[str] = Field(default_factory=list)  # 其他别名
    entity_type: Optional[str] = None        # company|government|research_institute|alliance|startup
    headquarters: Optional[str] = None       # 总部所在地


class ExtractedSupplyNode(BaseModel):
    """LLM 提取的供应链节点"""
    temp_id: str                             # 临时 ID（如 "n0", "n1"）
    tier_id: int                             # 层级编号
    layer_name: str                          # 层级名（如 "算力芯片"）
    node_name: str                           # 节点名（如 "GPU 设计"）
    description: Optional[str] = None


class IndustryContextResult(BaseModel):
    """Call 1 输出：产业上下文 + 玩家 + 供应链节点"""
    industry_chain: str                      # 产业链名（如 "AI"）
    tiers_covered: List[int] = Field(default_factory=list)  # 文章涉及的层级
    players: List[ExtractedPlayer] = Field(default_factory=list)
    supply_nodes: List[ExtractedSupplyNode] = Field(default_factory=list)


# ── Call 2 输出 ──────────────────────────────────────────────────────────────


class ExtractedIssue(BaseModel):
    """LLM 提取的产业议题"""
    temp_id: str                             # 如 "i0"
    supply_node_ref: Optional[str] = None    # 引用 SupplyNode temp_id（如 "n0"）
    issue_text: str                          # 议题描述（≤150字）
    severity: Optional[str] = None           # critical|high|medium|low
    status: Optional[str] = None             # active|emerging|resolved
    resolution_progress: Optional[str] = None
    summary: Optional[str] = None            # ≤15字摘要


class ExtractedTechRoute(BaseModel):
    """LLM 提取的技术路线"""
    temp_id: str                             # 如 "t0"
    supply_node_ref: Optional[str] = None
    route_name: str
    maturity: Optional[str] = None           # experimental|emerging|growth|mature|declining
    competing_routes: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class ExtractedMetric(BaseModel):
    """LLM 提取的产业指标"""
    temp_id: str                             # 如 "m0"
    supply_node_ref: Optional[str] = None
    player_ref: Optional[str] = None         # 引用 Player temp_id（如 "p0"）
    metric_name: str
    metric_value: str
    unit: Optional[str] = None
    time_reference: Optional[str] = None
    evidence_score: Optional[float] = None   # 0-1


class IndustryEntitiesResult(BaseModel):
    """Call 2 输出：议题 + 技术路线 + 指标"""
    issues: List[ExtractedIssue] = Field(default_factory=list)
    tech_routes: List[ExtractedTechRoute] = Field(default_factory=list)
    metrics: List[ExtractedMetric] = Field(default_factory=list)


# ── Call 3 输出 ──────────────────────────────────────────────────────────────


class IndustryEdge(BaseModel):
    """产业实体间 + 产业→观点跨层关系边"""
    source_type: str                         # player|supply_node|issue|tech_route|metric|fact|conclusion
    source_id: str                           # temp_id 或 DB ID 字符串
    target_type: str
    target_id: str
    edge_type: str                           # EdgeType 枚举值


class IndustryRelationshipResult(BaseModel):
    """Call 3 输出：跨层关系边"""
    edges: List[IndustryEdge] = Field(default_factory=list)
