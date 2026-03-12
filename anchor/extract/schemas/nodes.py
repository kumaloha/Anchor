"""
v8 Node/Edge Extraction Schema
================================
统一的节点+边提取 Pydantic schema，用于所有 6 个领域。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExtractedNode(BaseModel):
    """LLM 提取的单个节点"""
    temp_id: str           # "n0", "n1", ...
    node_type: str         # 必须属于该领域的合法类型
    claim: str             # ≤150字
    summary: str           # ≤15字
    abstract: Optional[str] = None  # 一句话总结 (≤50字)
    metadata: Optional[dict] = None
    valid_from: Optional[str] = None   # 生效日期 YYYY-MM-DD（LLM 判断）
    valid_until: Optional[str] = None  # 失效日期 YYYY-MM-DD（LLM 判断）


class NodeExtractionResult(BaseModel):
    """Call 1 输出：节点列表"""
    is_relevant_content: bool = True
    skip_reason: Optional[str] = None
    nodes: list[ExtractedNode] = []


VALID_EDGE_TYPES = {
    "causes", "produces", "derives", "supports", "contradicts",
    "implements", "constrains", "amplifies", "mitigates",
    "resolves", "measures", "competes",
}


class ExtractedEdge(BaseModel):
    """LLM 发现的单条边"""
    source_id: str         # temp_id 引用
    target_id: str
    edge_type: str = "causes"  # 12 种合法类型之一
    note: Optional[str] = None


class EdgeExtractionResult(BaseModel):
    """Call 2 输出：边列表 + 摘要"""
    edges: list[ExtractedEdge] = []
    summary: Optional[str] = None
    one_liner: Optional[str] = None   # ≤50字一句话总结
