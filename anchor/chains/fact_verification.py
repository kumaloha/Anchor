"""
事实验证 — Fact Verification
=============================
注意：v9 迁移后 ExtractionNode/Edge 已移除。
company 域跳过验证（直接写入专用表），其他域暂时禁用。
此模块保留供未来域专用验证管线使用。

一手信息门控保留：content_nature="一手信息" 的内容跳过验证。
"""

from __future__ import annotations

import datetime
import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import batch_chat_completions, chat_completion
from anchor.models import RawPost, _utcnow
from anchor.verify.web_searcher import format_search_results, web_search

_MAX_TOKENS = 1024

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYS_FACT = """\
你是专业事实核查员。给定一条事实陈述，结合搜索结果和训练知识，判断其可信度。

输出 JSON：
{"verdict": "credible|vague|unreliable|unavailable", "evidence": "≤200字证据摘要"}

分类标准：
  credible    — 有权威来源证实，数据精确
  vague       — 大致方向正确，但数据模糊或来源不够权威
  unreliable  — 来自未经证实的传言或与已知事实矛盾
  unavailable — 无法找到足够信息判断

严格输出 JSON，不加任何其他文字。"""

_SYS_PREDICTION = """\
你是专业预测核查员。给定一条预测型陈述，结合搜索结果和训练知识，对预测结果进行分类。

输出 JSON：
{"verdict": "pending|accurate|directional|off_target|wrong", "evidence": "≤200字证据摘要"}

分类标准：
  pending     — 预测时间窗口尚未到达，无法判断
  accurate    — 预测方向和量级均与现实吻合
  directional — 方向一致，但精度不足
  off_target  — 方向正确但量级偏差明显
  wrong       — 预测方向与现实相反

严格输出 JSON，不加任何其他文字。"""


# ---------------------------------------------------------------------------
# 搜索辅助：原文语言 + 英文交叉验证
# ---------------------------------------------------------------------------


def _get_original_claim(node: ExtractionNode) -> str | None:
    """从 metadata_json 提取 original_claim（非中文原文的原始语言 claim）。"""
    if not node.metadata_json:
        return None
    try:
        meta = json.loads(node.metadata_json)
        return meta.get("original_claim")
    except (json.JSONDecodeError, TypeError):
        return None


def _build_query(statement: str) -> str:
    """构建搜索查询：截短到 200 字，附加年份。"""
    base = statement[:200]
    current_year = str(datetime.date.today().year)
    if not re.search(r"20\d{2}", base):
        base = f"{base} {current_year}"
    return base


async def _cross_language_search(node: ExtractionNode) -> str:
    """双语搜索：原文语言 + 英文交叉验证。

    策略：
    1. 如果有 original_claim（非中文来源），用它搜索（原文语言）
    2. 用中文 claim 搜索（覆盖中文来源）
    3. 如果原文不是英文，额外做英文搜索交叉验证

    所有结果合并提供给 LLM。
    """
    sections: list[str] = []
    original_claim = _get_original_claim(node)

    # 1. 原文语言搜索（非中文时）
    if original_claim:
        query_orig = _build_query(original_claim)
        results = await web_search(query_orig, max_results=3)
        if results:
            sections.append(f"## 搜索结果（原文语言）\n\n{format_search_results(results)}")

        # 2. 英文交叉验证（如果原文也不是英文，构造英文关键词搜索）
        if not _looks_english(original_claim):
            en_query = _build_query(node.claim)  # claim 是中文，但也试英文
            en_results = await web_search(en_query, max_results=2)
            if en_results:
                sections.append(f"## 搜索结果（英文交叉验证）\n\n{format_search_results(en_results)}")
    else:
        # 中文原文：用 claim 搜索 + 英文交叉验证
        query_cn = _build_query(node.claim)
        results = await web_search(query_cn, max_results=3)
        if results:
            sections.append(f"## 搜索结果（中文）\n\n{format_search_results(results)}")

        # 英文交叉验证
        en_query = _build_query(node.claim)
        en_results = await web_search(en_query, max_results=2)
        if en_results:
            sections.append(f"## 搜索结果（英文交叉验证）\n\n{format_search_results(en_results)}")

    if sections:
        return "\n\n" + "\n\n".join(sections)
    return ""


def _looks_english(text: str) -> bool:
    """粗略判断文本是否为英文（ASCII 字母占比 > 60%）。"""
    if not text:
        return False
    ascii_count = sum(1 for c in text if c.isascii() and c.isalpha())
    total = sum(1 for c in text if c.isalpha())
    if total == 0:
        return False
    return ascii_count / total > 0.6


# ---------------------------------------------------------------------------
# 验证注册表
# ---------------------------------------------------------------------------

async def _verify_fact(node: ExtractionNode, session: AsyncSession) -> bool:
    """联网搜索核实事实类节点 — 注册表标记，批量模式下由 run_verification 统一调度。"""
    # 单节点退化路径（仅在非批量调用时使用）
    search_text = await _cross_language_search(node)
    result = await _call_llm(
        _SYS_FACT,
        f"请对以下事实陈述进行核查：\n\n## 待核查陈述\n{node.claim}\n{search_text}\n\n请基于搜索结果（优先）和训练知识输出 JSON。",
    )
    if result is None:
        return False
    node.verdict = _normalize(result.get("verdict"), {"credible", "vague", "unreliable", "unavailable"}, "unavailable")
    node.verdict_evidence = _safe_str(result.get("evidence"))
    node.verdict_verified_at = _utcnow()
    session.add(node)
    logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {node.verdict}")
    return True


async def _derive_verdict(node: ExtractionNode, session: AsyncSession) -> bool:
    """从支撑边推导判断类节点的 verdict。"""
    edges = list(
        (await session.exec(
            select(ExtractionEdge).where(ExtractionEdge.target_node_id == node.id)
        )).all()
    )

    if not edges:
        node.verdict = "pending"
        node.verdict_evidence = "无支撑节点"
        session.add(node)
        return True

    source_ids = [e.source_node_id for e in edges]
    source_nodes = list(
        (await session.exec(
            select(ExtractionNode).where(ExtractionNode.id.in_(source_ids))
        )).all()
    )

    verdicts = [n.verdict for n in source_nodes if n.verdict]

    if not verdicts:
        node.verdict = "pending"
        node.verdict_evidence = "支撑节点尚未验证"
        session.add(node)
        return True

    if any(v == "unreliable" for v in verdicts):
        verdict = "refuted"
        reason = "支撑节点中有不可靠信息"
    elif all(v in ("credible", "vague") for v in verdicts):
        verdict = "confirmed"
        reason = "所有支撑节点可信"
    elif all(v == "unavailable" for v in verdicts):
        verdict = "unverifiable"
        reason = "所有支撑节点无法验证"
    else:
        verdict = "partial"
        reason = "支撑节点验证结果混合"

    node.verdict = verdict
    node.verdict_evidence = reason
    node.verdict_verified_at = _utcnow()
    session.add(node)

    logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {verdict} ({reason})")
    return True


async def _monitor_prediction(node: ExtractionNode, session: AsyncSession) -> bool:
    """预测类节点验证 — 注册表标记，批量模式下由 run_verification 统一调度。"""
    search_text = await _cross_language_search(node)
    result = await _call_llm(
        _SYS_PREDICTION,
        f"请对以下预测型陈述进行核查分类：\n\n## 预测陈述\n{node.claim}\n{search_text}\n\n请基于搜索结果（优先）和训练知识输出 JSON。",
    )
    if result is None:
        return False
    node.verdict = _normalize(result.get("verdict"), {"pending", "accurate", "directional", "off_target", "wrong"}, "pending")
    node.verdict_evidence = _safe_str(result.get("evidence"))
    node.verdict_verified_at = _utcnow()
    session.add(node)
    logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {node.verdict}")
    return True


# (domain, node_type) → verification function
VERIFIABLE_TYPES: dict[tuple[str, str], object] = {
    ("expert", "事实"): _verify_fact,
    ("expert", "判断"): _derive_verdict,
    ("expert", "预测"): _monitor_prediction,
    ("company", "表现"): _verify_fact,
    ("policy", "反馈"): _verify_fact,
    ("futures", "供给"): _verify_fact,
    ("futures", "需求"): _verify_fact,
    ("technology", "效果性能"): _verify_fact,
}


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def run_verification(raw_post_id: int, session: AsyncSession) -> dict:
    """事实验证：对某帖子的所有可验证节点进行验证。

    注意：company 域已迁移到专用管线（无 ExtractionNode），直接跳过。
    其他域暂时禁用，此函数仅保留向后兼容。
    """
    logger.info(f"[Verification] Starting for raw_post_id={raw_post_id}")

    # ── 一手信息门控 ───────────────────────────────────────────────────────
    post = await session.get(RawPost, raw_post_id)
    if post and post.content_nature == "一手信息":
        logger.info(f"[Verification] Skip: 一手信息不验证 (raw_post_id={raw_post_id})")
        return {
            "raw_post_id": raw_post_id,
            "nodes_verified": 0,
            "skipped": True,
            "skip_reason": "一手信息不验证",
        }

    # ── company 域跳过（已迁移到专用管线，无 ExtractionNode）─────────────
    if post and post.content_domain == "公司":
        logger.info(f"[Verification] Skip: company domain uses dedicated pipeline")
        return {
            "raw_post_id": raw_post_id,
            "nodes_verified": 0,
            "skipped": True,
            "skip_reason": "company 域使用专用管线，跳过 Node/Edge 验证",
        }

    # ── 加载该帖子的所有节点（依赖旧 ExtractionNode 表，仅旧数据可用）────
    logger.warning(
        f"[Verification] ExtractionNode/Edge 已移除，"
        f"非 company 域的验证暂不可用 (raw_post_id={raw_post_id})"
    )
    return {
        "raw_post_id": raw_post_id,
        "nodes_verified": 0,
        "skipped": True,
        "skip_reason": "ExtractionNode/Edge 已移除，验证暂不可用",
    }

    # ── 以下代码保留供未来域专用验证管线参考 ────────────────────────────────
    nodes = []  # type: ignore[unreachable]

    # ── 分类：哪些需要搜索验证、哪些从边推导 ─────────────────────────────
    search_nodes: list[Node] = []       # 需要搜索 + LLM
    derive_nodes: list[Node] = []       # 从边推导

    for node in nodes:
        if node.verdict is not None:
            continue
        verify_fn = VERIFIABLE_TYPES.get((node.domain, node.node_type))
        if verify_fn is None:
            continue
        if verify_fn is _derive_verdict:
            derive_nodes.append(node)
        else:
            search_nodes.append(node)

    # ── Phase 1: 批量搜索（并发执行所有网络搜索）──────────────────────────
    import asyncio
    search_tasks = [_cross_language_search(n) for n in search_nodes]
    search_texts = await asyncio.gather(*search_tasks) if search_tasks else []

    # ── Phase 2: 批量 LLM 调用 ───────────────────────────────────────────
    llm_requests: list[tuple[str, str, int]] = []
    node_sys_prompts: list[str] = []     # 记录每个节点用的 system prompt

    for node, search_text in zip(search_nodes, search_texts):
        verify_fn = VERIFIABLE_TYPES.get((node.domain, node.node_type))
        if verify_fn is _monitor_prediction:
            sys_prompt = _SYS_PREDICTION
            user_prompt = f"""请对以下预测型陈述进行核查分类：

## 预测陈述
{node.claim}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""
        else:
            sys_prompt = _SYS_FACT
            user_prompt = f"""请对以下事实陈述进行核查：

## 待核查陈述
{node.claim}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

        llm_requests.append((sys_prompt, user_prompt, _MAX_TOKENS))
        node_sys_prompts.append(sys_prompt)

    nodes_verified = 0

    if llm_requests:
        logger.info(f"[Verification] Batch LLM: {len(llm_requests)} requests")
        llm_results = await batch_chat_completions(llm_requests)

        for node, raw_resp, sys_prompt in zip(search_nodes, llm_results, node_sys_prompts):
            if raw_resp is None:
                continue

            result = _parse_json(raw_resp.content)
            if result is None:
                continue

            if sys_prompt == _SYS_PREDICTION:
                valid_set = {"pending", "accurate", "directional", "off_target", "wrong"}
                default = "pending"
            else:
                valid_set = {"credible", "vague", "unreliable", "unavailable"}
                default = "unavailable"

            node.verdict = _normalize(result.get("verdict"), valid_set, default)
            node.verdict_evidence = _safe_str(result.get("evidence"))
            node.verdict_verified_at = _utcnow()
            session.add(node)
            nodes_verified += 1
            logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {node.verdict}")

    # ── Phase 3: 边推导（不需要 LLM）─────────────────────────────────────
    for node in derive_nodes:
        changed = await _derive_verdict(node, session)
        if changed:
            nodes_verified += 1

    await session.flush()
    await session.commit()

    logger.info(
        f"[Verification] Done for raw_post_id={raw_post_id}: "
        f"nodes_verified={nodes_verified}"
    )

    return {
        "raw_post_id": raw_post_id,
        "nodes_verified": nodes_verified,
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _call_llm(system: str, user: str) -> dict | None:
    resp = await chat_completion(system=system, user=user, max_tokens=_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


def _parse_json(raw: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = raw.strip()
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[Verification] JSON parse error: {exc}")
        return None


def _normalize(value: str | None, valid: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in valid else default


def _safe_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None
