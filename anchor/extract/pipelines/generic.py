"""
pipelines/generic.py — 统一 2-call 提取管线
=============================================
所有 6 个领域使用同一管线，只换提示词。

Call 1: 提取节点（长文档自动分段，多次调用）
Call 2: 发现边 + 生成摘要（一次调用，基于全部节点）
"""

from __future__ import annotations

import json

from loguru import logger
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import DOMAIN_NODE_TYPES, Edge, Node, RawPost, _utcnow
from anchor.extract.schemas.nodes import (
    EdgeExtractionResult,
    ExtractedNode,
    NodeExtractionResult,
)

import re as _re

_CALL1_TOKENS = 16000
_CALL2_TOKENS = 8000

# ── 通用智能分段 ──────────────────────────────────────────────────────────

_CHUNK_TARGET = 10000  # 每段目标字符数

# 多级标题/结构探测模式（优先级从高到低）
_HEADING_PATTERNS = [
    # Markdown headers
    _re.compile(r'^#{1,3}\s+.+', _re.MULTILINE),
    # 中文大章节：一、二、三...
    _re.compile(r'^\s*[一二三四五六七八九十]{1,3}\s*[、，]\s*.+', _re.MULTILINE),
    # 中文小节：（一）（二）...
    _re.compile(r'^[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]\s*.+', _re.MULTILINE),
    # 英文编号章节：1. / 2. / Part I / Section 1 / Item 1
    _re.compile(r'^\s*(?:Part\s+[IVX]+|Section\s+\d+|Item\s+\d+[A-Z]?)\b.*', _re.MULTILINE),
    # 加粗标题行（Markdown **Title** 或 __Title__）
    _re.compile(r'^\s*(?:\*\*|__)[A-Z\u4e00-\u9fff].{2,60}(?:\*\*|__)\s*$', _re.MULTILINE),
    # 全大写标题行（英文报告常见）
    _re.compile(r'^[A-Z][A-Z\s,&]{10,80}$', _re.MULTILINE),
]


def _find_section_boundaries(content: str) -> list[int]:
    """多级结构探测：返回所有可用切割位置（字符偏移量列表），按位置排序去重。"""
    positions: set[int] = set()
    for pattern in _HEADING_PATTERNS:
        for m in pattern.finditer(content):
            positions.add(m.start())
    return sorted(positions)


def _greedy_merge(content: str, boundaries: list[int], target: int) -> list[str]:
    """贪心合并：沿结构边界切割，每段尽量接近 target 字符。"""
    if not boundaries:
        return [content]

    # 前言（第一个标题之前的内容）
    segments: list[tuple[int, int]] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        segments.append((start, end))

    preamble = content[:boundaries[0]].strip()
    chunks: list[str] = []
    current = (preamble + "\n\n") if preamble else ""

    for start, end in segments:
        seg = content[start:end]
        if len(current) + len(seg) > target and current.strip():
            chunks.append(current.strip())
            current = ""
        current += seg

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _smart_chunk(content: str, target: int = _CHUNK_TARGET) -> list[str] | None:
    """通用智能分段：优先沿文档结构边界切割，回退到段落边界。

    策略：
    1. 探测 Markdown/中文/英文 各种标题模式
    2. 沿标题边界贪心合并，每段 ≤ target 字符
    3. 若标题不够（单段仍超长），在段落双换行处二次切割
    4. 返回 None 表示不需要切割
    """
    if len(content) <= target:
        return None

    boundaries = _find_section_boundaries(content)

    if len(boundaries) >= 2:
        chunks = _greedy_merge(content, boundaries, target)
    else:
        chunks = [content]

    # 二次切割：对仍超长的段落在双换行处再分
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= target * 1.3:  # 允许 30% 超标
            final.append(chunk)
        else:
            paragraphs = _re.split(r'\n{2,}', chunk)
            current = ""
            for para in paragraphs:
                if len(current) + len(para) + 2 > target and current.strip():
                    final.append(current.strip())
                    current = ""
                current += para + "\n\n"
            if current.strip():
                final.append(current.strip())

    if len(final) <= 1:
        return None

    return final


# ── Call 1 单次调用 ──────────────────────────────────────────────────────

async def _call1_single(
    prompt_module,
    content: str,
    platform: str,
    author: str,
    today: str,
    valid_types: set[str],
    call_llm,
    parse_json,
    id_offset: int = 0,
    existing_themes: list[str] | None = None,
) -> list[ExtractedNode]:
    """对单段内容执行 Call 1，返回有效节点列表。"""
    user1 = prompt_module.build_user_message_call1(content, platform, author, today)

    # 分段模式：告诉 LLM 已有哪些主旨，避免重复
    if existing_themes:
        themes_str = "、".join(existing_themes)
        user1 += (
            f"\n\n## 已提取的主旨（来自前面的段落，不要重复创建）\n"
            f"{themes_str}\n"
            f"如果本段内容属于上述已有主旨，直接使用相同的 summary 前缀（如 [内需]）。"
            f"只有出现全新主题时才创建新的主旨节点。"
        )

    raw1 = await call_llm(prompt_module.SYSTEM_CALL1, user1, _CALL1_TOKENS)
    if raw1 is None:
        logger.warning("[Generic] Call 1 chunk LLM returned None")
        return []

    result1 = parse_json(raw1, NodeExtractionResult, "generic_call1")
    if result1 is None:
        logger.warning("[Generic] Call 1 chunk parse failed")
        return []

    if not result1.is_relevant_content:
        return []

    valid_nodes = []
    for n in result1.nodes:
        if n.node_type not in valid_types:
            logger.warning(f"[Generic] Invalid node_type={n.node_type!r}, skipping")
            continue
        # 重编号 temp_id 避免跨段冲突
        if id_offset > 0:
            n.temp_id = f"n{int(n.temp_id.lstrip('n')) + id_offset}"
        valid_nodes.append(n)

    return valid_nodes


# ── 主旨去重 ─────────────────────────────────────────────────────────────

def _extract_theme_prefix(summary: str) -> str | None:
    """提取 [xxx] 前缀，用于匹配同主旨节点。"""
    if "]" in summary:
        return summary.split("]")[0] + "]"
    return None


def _dedup_theme_nodes(nodes: list[ExtractedNode]) -> list[ExtractedNode]:
    """合并重复的主旨节点：同一 [前缀] 只保留第一个主旨，后续主旨的子节点归入第一个。"""
    seen_prefixes: dict[str, str] = {}  # prefix → first theme's temp_id
    remap: dict[str, str] = {}  # duplicate theme temp_id → first theme temp_id
    result = []

    for n in nodes:
        if n.node_type == "主旨":
            prefix = _extract_theme_prefix(n.summary)
            if prefix and prefix in seen_prefixes:
                # 重复主旨，跳过节点但记录映射
                remap[n.temp_id] = seen_prefixes[prefix]
                logger.info(f"[Generic] Dedup: merge '{n.summary}' into existing {prefix}")
                continue
            if prefix:
                seen_prefixes[prefix] = n.temp_id
        result.append(n)

    if remap:
        logger.info(f"[Generic] Deduped {len(remap)} duplicate themes")

    return result


# ── 主入口 ───────────────────────────────────────────────────────────────

async def extract_generic(
    raw_post: RawPost,
    session: AsyncSession,
    content: str,
    platform: str,
    author: str,
    today: str,
    domain: str,
    author_intent: str | None = None,
    force: bool = False,
) -> dict | None:
    """统一提取入口：2-call LLM pipeline → Node/Edge 写入 DB。

    Returns:
        dict with keys: is_relevant_content, skip_reason, nodes, edges, summary
        or None if LLM call failed.
    """
    from anchor.extract.prompts.domains import DOMAIN_PROMPTS
    from anchor.extract.pipelines._base import call_llm, parse_json

    prompt_module = DOMAIN_PROMPTS.get(domain)
    if prompt_module is None:
        logger.error(f"[Generic] Unknown domain: {domain}")
        return None

    valid_types = set(DOMAIN_NODE_TYPES.get(domain, []))

    # ── 清除旧数据 ────────────────────────────────────────────────────────
    await session.exec(delete(Edge).where(Edge.added_by_post_id == raw_post.id))
    await session.exec(delete(Node).where(Node.raw_post_id == raw_post.id))
    await session.flush()

    # ── Call 1：提取节点（支持分段）────────────────────────────────────────
    chunks = None
    if hasattr(prompt_module, "chunk_content"):
        chunks = prompt_module.chunk_content(content)

    # 通用智能分段：若域特定分段未触发但内容超长，自动检测文档结构并切割
    if chunks is None and len(content) > _CHUNK_TARGET:
        chunks = _smart_chunk(content, target=_CHUNK_TARGET)
        if chunks:
            logger.info(f"[Generic] Smart chunking: {len(content)} chars → {len(chunks)} chunks")

    valid_nodes: list[ExtractedNode] = []

    if chunks:
        logger.info(f"[Generic] Long content ({len(content)} chars) → {len(chunks)} chunks")
        id_offset = 0
        existing_themes: list[str] = []
        for i, chunk in enumerate(chunks):
            logger.info(f"[Generic] Call 1 chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
            chunk_nodes = await _call1_single(
                prompt_module, chunk, platform, author, today,
                valid_types, call_llm, parse_json,
                id_offset=id_offset,
                existing_themes=existing_themes if existing_themes else None,
            )
            # 收集本段新增的主旨
            for n in chunk_nodes:
                if n.node_type == "主旨":
                    existing_themes.append(n.summary)
            valid_nodes.extend(chunk_nodes)
            id_offset += len(chunk_nodes)
    else:
        # 单次调用
        valid_nodes = await _call1_single(
            prompt_module, content, platform, author, today,
            valid_types, call_llm, parse_json,
        )

    # ── 去重：合并重复主旨节点 ──────────────────────────────────────────
    if chunks and valid_nodes:
        valid_nodes = _dedup_theme_nodes(valid_nodes)

    if not valid_nodes:
        logger.info("[Generic] No valid nodes extracted")
        raw_post.is_processed = True
        raw_post.processed_at = _utcnow()
        session.add(raw_post)
        await session.flush()
        return {
            "is_relevant_content": len(valid_nodes) == 0 and chunks is None,
            "skip_reason": "no valid nodes",
            "nodes": [],
            "edges": [],
            "summary": None,
        }

    # ── 写入 Node 表 ──────────────────────────────────────────────────────
    temp_id_to_db_id: dict[str, int] = {}
    db_nodes: list[Node] = []

    for n in valid_nodes:
        node = Node(
            raw_post_id=raw_post.id,
            domain=domain,
            node_type=n.node_type,
            claim=n.claim[:300],
            summary=n.summary[:30],
            abstract=n.abstract[:100] if n.abstract else None,
            metadata_json=json.dumps(n.metadata, ensure_ascii=False) if n.metadata else None,
        )
        session.add(node)
        db_nodes.append(node)

    await session.flush()

    for n, db_node in zip(valid_nodes, db_nodes):
        temp_id_to_db_id[n.temp_id] = db_node.id

    logger.info(f"[Generic] Call 1: {len(db_nodes)} nodes written (domain={domain})")

    # ── Call 2：发现边 + 生成摘要（大文档自动分批）──────────────────────────
    nodes_for_llm = [
        {"temp_id": n.temp_id, "node_type": n.node_type, "claim": n.claim, "summary": n.summary, "abstract": n.abstract}
        for n in valid_nodes
    ]

    # 分批策略：节点过多时拆成多批 Call 2，每批 ≤40 节点
    _CALL2_BATCH = 40
    summary = None
    one_liner = None
    edges_written = 0

    if len(nodes_for_llm) <= _CALL2_BATCH:
        # 单批
        node_batches = [nodes_for_llm]
    else:
        # 多批：按 temp_id 顺序拆分
        node_batches = [
            nodes_for_llm[i:i + _CALL2_BATCH]
            for i in range(0, len(nodes_for_llm), _CALL2_BATCH)
        ]
        logger.info(f"[Generic] Call 2 batched: {len(nodes_for_llm)} nodes → {len(node_batches)} batches")

    content_for_call2 = content[:15000] if len(content) > 15000 else content

    for batch_idx, batch in enumerate(node_batches):
        nodes_json = json.dumps(batch, ensure_ascii=False, indent=2)
        user2 = prompt_module.build_user_message_call2(content_for_call2, nodes_json)
        raw2 = await call_llm(prompt_module.SYSTEM_CALL2, user2, _CALL2_TOKENS)

        if raw2 is not None:
            result2 = parse_json(raw2, EdgeExtractionResult, "generic_call2")
            if result2 is not None:
                # 只取第一批的摘要（最完整）
                if batch_idx == 0:
                    summary = result2.summary
                    one_liner = result2.one_liner

                for e in result2.edges:
                    src_id = temp_id_to_db_id.get(e.source_id)
                    tgt_id = temp_id_to_db_id.get(e.target_id)
                    if src_id is None or tgt_id is None:
                        logger.warning(
                            f"[Generic] Edge ref invalid: {e.source_id}→{e.target_id}"
                        )
                        continue
                    if src_id == tgt_id:
                        continue

                    edge = Edge(
                        source_node_id=src_id,
                        target_node_id=tgt_id,
                        note=e.note[:80] if e.note else None,
                        added_by_post_id=raw_post.id,
                    )
                    session.add(edge)
                    edges_written += 1

                await session.flush()
        else:
            logger.warning(f"[Generic] Call 2 batch {batch_idx+1} LLM returned None")

    # ── 更新 RawPost ──────────────────────────────────────────────────────
    raw_post.is_processed = True
    raw_post.processed_at = _utcnow()
    if summary:
        raw_post.content_summary = summary
    session.add(raw_post)
    await session.commit()

    logger.info(
        f"[Generic] Done: {len(db_nodes)} nodes, {edges_written} edges, "
        f"domain={domain}"
    )

    return {
        "is_relevant_content": True,
        "skip_reason": None,
        "nodes": db_nodes,
        "edges": edges_written,
        "summary": summary,
        "one_liner": one_liner,
    }
