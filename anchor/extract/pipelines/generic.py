"""
pipelines/generic.py — 统一 2-call 提取管线（已废弃）
=====================================================
注意：v9 迁移后此管线不再用于新域。保留供参考。
所有已启用域使用各自的专用管线。

原架构：所有 6 个领域使用同一管线，只换提示词。
Call 1: 提取节点（长文档自动分段，多次调用）
Call 2: 发现边 + 生成摘要（一次调用，基于全部节点）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from loguru import logger
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import RawPost, _utcnow
from anchor.extract.schemas.nodes import (
    EdgeExtractionResult,
    ExtractedNode,
    NodeExtractionResult,
)

# DOMAIN_NODE_TYPES 已从 models.py 移除，此处内联保留供 generic pipeline 使用
DOMAIN_NODE_TYPES = {
    "policy":     ["主旨", "目标", "战略", "战术", "资源", "考核", "约束", "反馈", "外溢"],
    "industry":   ["格局", "驱动", "趋势", "技术路线", "资金流向", "机会威胁", "标的"],
    "technology": ["问题", "方案", "效果性能", "局限场景", "玩家"],
    "futures":    ["供给", "需求", "库存", "头寸", "冲击", "缺口"],
    "company":    ["叙事"],
    "expert":     ["事实", "判断", "预测", "建议"],
}

import re as _re
from datetime import date as _date

_CALL1_TOKENS = 16000


def _parse_date(s: str | None) -> _date | None:
    """Parse YYYY-MM-DD string to date, return None on failure."""
    if not s or s == "null":
        return None
    try:
        return _date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


async def _resolve_authority(raw_post: RawPost, session) -> int | None:
    """计算权威等级：一手信息→0，其他→作者 credibility_tier。"""
    if raw_post.content_nature == "一手信息":
        return 0
    from anchor.models import Author
    from sqlmodel import select
    author = (await session.exec(
        select(Author).where(
            Author.platform == raw_post.source,
            Author.name == raw_post.author_name,
        )
    )).first()
    if author and author.credibility_tier is not None:
        return author.credibility_tier
    return None
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


# ── Batch 模式：分段 Call 1 批量提交 ─────────────────────────────────────


async def _call1_chunked_batch(
    prompt_module,
    chunks: list[str],
    platform: str,
    author: str,
    today: str,
    valid_types: set[str],
    call_llm,
    parse_json,
) -> list[ExtractedNode]:
    """分段 Call 1：使用 Batch API 批量提交所有 chunk，一次性获取结果。

    策略：
    - 将所有 chunk 的 Call 1 请求打包提交 batch_chat_completions
    - 解析所有结果，重编号 temp_id，收集主旨去重
    """
    from anchor.extract.pipelines._base import call_llm_batch

    # 构建所有请求
    batch_requests: list[tuple[str, str, int]] = []
    for chunk in chunks:
        user1 = prompt_module.build_user_message_call1(chunk, platform, author, today)
        batch_requests.append((prompt_module.SYSTEM_CALL1, user1, _CALL1_TOKENS))

    logger.info(f"[Generic] Call 1 batch: {len(batch_requests)} chunks")
    raw_results = await call_llm_batch(batch_requests)

    # 解析结果，重编号
    all_nodes: list[ExtractedNode] = []
    id_offset = 0

    for i, raw in enumerate(raw_results):
        if raw is None:
            logger.warning(f"[Generic] Call 1 chunk {i+1}/{len(chunks)} returned None")
            continue

        result1 = parse_json(raw, NodeExtractionResult, f"generic_call1_chunk{i}")
        if result1 is None or not result1.is_relevant_content:
            continue

        for n in result1.nodes:
            if n.node_type not in valid_types:
                logger.warning(f"[Generic] Invalid node_type={n.node_type!r}, skipping")
                continue
            if id_offset > 0:
                n.temp_id = f"n{int(n.temp_id.lstrip('n')) + id_offset}"
            all_nodes.append(n)

        id_offset += len(result1.nodes)

    return all_nodes


async def _call2_batch(
    prompt_module,
    node_batches: list[list[dict]],
    content_for_call2: str,
    call_llm,
    parse_json,
) -> tuple[list[EdgeExtractionResult], str | None, str | None]:
    """Call 2 多批次批量提交。返回 (edge_results, summary, one_liner)。"""
    from anchor.extract.pipelines._base import call_llm_batch

    batch_requests: list[tuple[str, str, int]] = []
    for batch in node_batches:
        nodes_json = json.dumps(batch, ensure_ascii=False, indent=2)
        user2 = prompt_module.build_user_message_call2(content_for_call2, nodes_json)
        batch_requests.append((prompt_module.SYSTEM_CALL2, user2, _CALL2_TOKENS))

    logger.info(f"[Generic] Call 2 batch: {len(batch_requests)} batches")
    raw_results = await call_llm_batch(batch_requests)

    edge_results = []
    summary = None
    one_liner = None

    for i, raw in enumerate(raw_results):
        if raw is None:
            logger.warning(f"[Generic] Call 2 batch {i+1} returned None")
            continue
        result2 = parse_json(raw, EdgeExtractionResult, f"generic_call2_batch{i}")
        if result2 is not None:
            edge_results.append(result2)
            if i == 0:
                summary = result2.summary
                one_liner = result2.one_liner

    return edge_results, summary, one_liner


# ── Compute-only 阶段（纯 LLM 调用，不涉及 DB）──────────────────────────


@dataclass
class ExtractionComputeResult:
    """LLM 提取的中间结果（不含 DB 写入）。"""
    is_relevant: bool = False
    skip_reason: str | None = None
    valid_nodes: list[ExtractedNode] = field(default_factory=list)
    edge_results: list[EdgeExtractionResult] = field(default_factory=list)
    summary: str | None = None
    one_liner: str | None = None


async def extract_generic_compute(
    content: str,
    platform: str,
    author: str,
    today: str,
    domain: str,
) -> ExtractionComputeResult:
    """纯 LLM 计算阶段：提取节点 + 发现边，不涉及任何 DB 操作。

    可安全并发调用。返回中间结果，由 extract_generic_write 写入 DB。
    """
    from anchor.extract.prompts.domains import DOMAIN_PROMPTS
    from anchor.extract.pipelines._base import call_llm, parse_json

    result = ExtractionComputeResult()

    prompt_module = DOMAIN_PROMPTS.get(domain)
    if prompt_module is None:
        logger.error(f"[Generic] Unknown domain: {domain}")
        return result

    valid_types = set(DOMAIN_NODE_TYPES.get(domain, []))

    # ── Call 1：提取节点 ──────────────────────────────────────────────────
    chunks = None
    if hasattr(prompt_module, "chunk_content"):
        chunks = prompt_module.chunk_content(content)

    if chunks is None and len(content) > _CHUNK_TARGET:
        chunks = _smart_chunk(content, target=_CHUNK_TARGET)
        if chunks:
            logger.info(f"[Generic] Smart chunking: {len(content)} chars → {len(chunks)} chunks")

    if chunks:
        logger.info(f"[Generic] Long content ({len(content)} chars) → {len(chunks)} chunks")
        valid_nodes = await _call1_chunked_batch(
            prompt_module, chunks, platform, author, today,
            valid_types, call_llm, parse_json,
        )
    else:
        valid_nodes = await _call1_single(
            prompt_module, content, platform, author, today,
            valid_types, call_llm, parse_json,
        )

    if chunks and valid_nodes:
        valid_nodes = _dedup_theme_nodes(valid_nodes)

    if not valid_nodes:
        logger.info("[Generic] No valid nodes extracted")
        result.skip_reason = "no valid nodes"
        return result

    result.valid_nodes = valid_nodes
    result.is_relevant = True

    logger.info(f"[Generic] Call 1 compute: {len(valid_nodes)} nodes (domain={domain})")

    # ── Call 2：发现边 + 摘要 ─────────────────────────────────────────────
    nodes_for_llm = [
        {"temp_id": n.temp_id, "node_type": n.node_type, "claim": n.claim,
         "summary": n.summary, "abstract": n.abstract}
        for n in valid_nodes
    ]

    _CALL2_BATCH_SIZE = 40
    content_for_call2 = content[:15000] if len(content) > 15000 else content

    if len(nodes_for_llm) <= _CALL2_BATCH_SIZE:
        node_batches = [nodes_for_llm]
    else:
        node_batches = [
            nodes_for_llm[i:i + _CALL2_BATCH_SIZE]
            for i in range(0, len(nodes_for_llm), _CALL2_BATCH_SIZE)
        ]
        logger.info(f"[Generic] Call 2: {len(nodes_for_llm)} nodes → {len(node_batches)} batches")

    edge_results, summary, one_liner = await _call2_batch(
        prompt_module, node_batches, content_for_call2, call_llm, parse_json,
    )

    result.edge_results = edge_results
    result.summary = summary
    result.one_liner = one_liner

    logger.info(f"[Generic] Compute done: {len(valid_nodes)} nodes, domain={domain}")
    return result


# ── Write-only 阶段（纯 DB 写入，无 LLM 调用）──────────────────────────


async def extract_generic_write(
    raw_post: RawPost,
    session: AsyncSession,
    domain: str,
    compute_result: ExtractionComputeResult,
) -> dict | None:
    """DB 写入阶段（已废弃）。

    ExtractionNode/Edge 模型已移除。此函数仅保留签名供向后兼容。
    所有已启用域应使用各自的专用 write 函数。
    """
    raise NotImplementedError(
        f"extract_generic_write 已废弃：ExtractionNode/Edge 模型已移除。"
        f"域 '{domain}' 应使用专用管线。"
    )


# ── 主入口（向后兼容，串行 compute + write）──────────────────────────────

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
    """统一提取入口（已废弃）。

    ExtractionNode/Edge 模型已移除。所有已启用域应使用各自的专用管线。
    """
    raise NotImplementedError(
        f"extract_generic 已废弃：ExtractionNode/Edge 模型已移除。"
        f"域 '{domain}' 应使用专用管线。"
    )
