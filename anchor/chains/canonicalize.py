"""
chains/canonicalize.py — 节点归一化（embedding 预筛 + LLM 精判）
================================================================
从 extraction_nodes 中筛选有效且权威的节点，两两比较是否为同一事物，
相同的节点通过 canonical_node_id 指向同一个主节点（Union-Find 风格）。

流程：
1. 筛选候选节点
2. Embedding 预筛：余弦相似度 > 0.8 的对才进入 LLM 精判
3. LLM 精判：is_same → Union-Find 合并
4. 写回 canonical_node_id

若 embedding API 不可用，退化为全量 N² LLM 比较。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional

from loguru import logger
from pydantic import BaseModel
from sqlmodel import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.extract.pipelines._base import call_llm, parse_json
from anchor.models import ExtractionNode

_EMBEDDING_THRESHOLD = 0.8
_EMBEDDING_BATCH_SIZE = 10  # 每批 embedding 请求的文本数（DashScope 限制 ≤10）


# ── Schema ──────────────────────────────────────────────────────────────────


class SimilarityResult(BaseModel):
    """LLM 判断两个节点是否指向同一事物"""

    is_same: bool
    reason: Optional[str] = None


# ── Prompt ──────────────────────────────────────────────────────────────────

_SYSTEM = """\
你是一个信息归一化专家。给定两个从不同文章中提取的信息节点，判断它们是否指向同一个事物/事件/观点。

【判断标准】
- "同一事物"的含义：如果两个节点在知识图谱中应该合并为一个节点，则判定为 is_same=true
- 相同事件的不同角度描述 → is_same=true
- 相同政策/决策的不同表述 → is_same=true
- 相同公司/人物的同一行为 → is_same=true
- 相似但不同的事件（如同类型但不同时间的政策）→ is_same=false
- 包含关系（一个是另一个的子集）→ is_same=false
- 仅主题相同但具体内容不同 → is_same=false

输出合法 JSON，不加任何其他文字。\
"""


def _build_user_message(node_a: ExtractionNode, node_b: ExtractionNode) -> str:
    return f"""\
## 节点 A
领域：{node_a.domain}
类型：{node_a.node_type}
摘要：{node_a.summary}
内容：{node_a.claim}

## 节点 B
领域：{node_b.domain}
类型：{node_b.node_type}
摘要：{node_b.summary}
内容：{node_b.claim}

## 任务
判断这两个节点是否指向同一个事物/事件/观点。

输出格式：
```json
{{"is_same": true/false, "reason": "≤50字说明"}}
```\
"""


# ── 向量工具 ────────────────────────────────────────────────────────────────


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _get_embedding_vectors(
    nodes: list[ExtractionNode],
) -> list[list[float]] | None:
    """批量获取节点 claim 的 embedding 向量，分批调用。"""
    from anchor.llm_client import get_embeddings

    texts = [f"{n.summary}: {n.claim}" for n in nodes]
    all_vectors: list[list[float]] = []

    for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + _EMBEDDING_BATCH_SIZE]
        vectors = await get_embeddings(batch)
        if vectors is None:
            return None
        all_vectors.extend(vectors)

    return all_vectors


def _find_candidate_pairs(
    nodes: list[ExtractionNode],
    vectors: list[list[float]],
    threshold: float,
) -> list[tuple[int, int]]:
    """从 embedding 向量中找出相似度 > threshold 的节点对索引。"""
    pairs: list[tuple[int, int]] = []
    n = len(nodes)
    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(vectors[i], vectors[j])
            if sim > threshold:
                pairs.append((i, j))
    return pairs


# ── Union-Find 辅助 ────────────────────────────────────────────────────────


def _find_root(parent: dict[int, int], x: int) -> int:
    """路径压缩的 Union-Find root 查找。"""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: dict[int, int], a: int, b: int) -> None:
    """合并两个节点所在的集合，ID 小的做 root。"""
    ra, rb = _find_root(parent, a), _find_root(parent, b)
    if ra != rb:
        if ra > rb:
            ra, rb = rb, ra
        parent[rb] = ra


# ── 主逻辑 ──────────────────────────────────────────────────────────────────


async def canonicalize_nodes(session: AsyncSession) -> int:
    """执行节点归一化，返回合并的节点对数。

    流程：
    1. 筛选有效 + 权威节点
    2. Embedding 预筛（相似度 > 0.8 的对）
    3. LLM 精判 + Union-Find 合并
    4. 写回 canonical_node_id
    """
    today = date.today()

    # 1. 筛选节点
    stmt = select(ExtractionNode).where(
        or_(
            ExtractionNode.valid_until >= today,
            ExtractionNode.valid_until.is_(None),  # type: ignore
        ),
        or_(
            ExtractionNode.authority.is_(None),  # type: ignore
            ExtractionNode.authority <= 1,
        ),
    )
    nodes = list((await session.exec(stmt)).all())
    logger.info(f"canonicalize: {len(nodes)} candidate nodes")

    if len(nodes) < 2:
        return 0

    # 2. 初始化 Union-Find
    parent: dict[int, int] = {n.id: n.id for n in nodes}

    # 3. Embedding 预筛
    vectors = await _get_embedding_vectors(nodes)
    total_pairs = len(nodes) * (len(nodes) - 1) // 2

    if vectors is not None:
        candidate_pairs = _find_candidate_pairs(nodes, vectors, _EMBEDDING_THRESHOLD)
        logger.info(
            f"canonicalize: embedding 预筛 {len(candidate_pairs)}/{total_pairs} pairs "
            f"(threshold={_EMBEDDING_THRESHOLD})"
        )
    else:
        # embedding 不可用，退化为全量 N² 比较
        logger.warning("canonicalize: embedding 不可用，退化为全量 N² 比较")
        candidate_pairs = [
            (i, j) for i in range(len(nodes)) for j in range(i + 1, len(nodes))
        ]

    # 4. LLM 精判
    merge_count = 0
    llm_calls = 0
    for i, j in candidate_pairs:
        na, nb = nodes[i], nodes[j]

        # 已在同一组则跳过
        if _find_root(parent, na.id) == _find_root(parent, nb.id):
            continue

        # 快速预筛：summary 完全相同直接合并
        if na.summary == nb.summary and na.domain == nb.domain:
            _union(parent, na.id, nb.id)
            merge_count += 1
            logger.debug(
                f"canonicalize: quick merge [{na.id}] ≡ [{nb.id}] (same summary)"
            )
            continue

        # LLM 比较
        user_msg = _build_user_message(na, nb)
        raw = await call_llm(_SYSTEM, user_msg, max_tokens=200)
        llm_calls += 1
        if raw is None:
            continue

        result = parse_json(raw, SimilarityResult, "canonicalize")
        if result and result.is_same:
            _union(parent, na.id, nb.id)
            merge_count += 1
            logger.info(
                f"canonicalize: merge [{na.id}] ≡ [{nb.id}] — {result.reason}"
            )

    logger.info(f"canonicalize: {llm_calls} LLM calls, {merge_count} merges")

    # 5. 写回 canonical_node_id
    updated = 0
    for node in nodes:
        root = _find_root(parent, node.id)
        if node.canonical_node_id != root:
            node.canonical_node_id = root
            session.add(node)
            updated += 1

    if updated:
        await session.flush()
        logger.info(f"canonicalize: updated {updated} nodes")

    return merge_count
