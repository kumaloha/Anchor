"""
事实验证 — Fact Verification（v8 Node/Edge 架构）
===================================================
输入：raw_post_id
输出：Node.verdict 字段写入 DB

基于 (domain, node_type) 的验证注册表：
  ("expert", "事实")  → verify_fact     — 联网搜索核实
  ("expert", "判断")  → derive_verdict  — 从支撑边推导
  ("expert", "预测")  → monitor_prediction — 时间窗口监控
  ("company", "表现") → verify_fact     — 财务数据核实
  ("policy", "反馈")  → verify_fact     — 执行追踪

一手信息门控保留：content_nature="一手信息" 的内容跳过验证。

用法：
  async with AsyncSessionLocal() as session:
      result = await run_verification(raw_post_id=1, session=session)
"""

from __future__ import annotations

import datetime
import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Edge, Node, RawPost, _utcnow
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
# 验证注册表
# ---------------------------------------------------------------------------

async def _verify_fact(node: Node, session: AsyncSession) -> bool:
    """联网搜索核实事实类节点。"""
    query = _build_query(node.claim)
    search_text = await _search(query)

    prompt = f"""请对以下事实陈述进行核查：

## 待核查陈述
{node.claim}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_FACT, prompt)
    if result is None:
        return False

    verdict = _normalize(
        result.get("verdict"),
        {"credible", "vague", "unreliable", "unavailable"},
        "unavailable",
    )

    node.verdict = verdict
    node.verdict_evidence = _safe_str(result.get("evidence"))
    node.verdict_verified_at = _utcnow()
    session.add(node)

    logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {verdict}")
    return True


async def _derive_verdict(node: Node, session: AsyncSession) -> bool:
    """从支撑边推导判断类节点的 verdict。"""
    # 查找所有指向该节点的边
    edges = list(
        (await session.exec(
            select(Edge).where(Edge.target_node_id == node.id)
        )).all()
    )

    if not edges:
        node.verdict = "pending"
        node.verdict_evidence = "无支撑节点"
        session.add(node)
        return True

    # 获取所有源节点的 verdict
    source_ids = [e.source_node_id for e in edges]
    source_nodes = list(
        (await session.exec(
            select(Node).where(Node.id.in_(source_ids))
        )).all()
    )

    verdicts = [n.verdict for n in source_nodes if n.verdict]

    if not verdicts:
        node.verdict = "pending"
        node.verdict_evidence = "支撑节点尚未验证"
        session.add(node)
        return True

    # 聚合规则
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


async def _monitor_prediction(node: Node, session: AsyncSession) -> bool:
    """预测类节点验证。"""
    query = _build_query(node.claim)
    search_text = await _search(query)

    prompt = f"""请对以下预测型陈述进行核查分类：

## 预测陈述
{node.claim}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_PREDICTION, prompt)
    if result is None:
        return False

    verdict = _normalize(
        result.get("verdict"),
        {"pending", "accurate", "directional", "off_target", "wrong"},
        "pending",
    )

    node.verdict = verdict
    node.verdict_evidence = _safe_str(result.get("evidence"))
    node.verdict_verified_at = _utcnow()
    session.add(node)

    logger.info(f"[Verification] Node id={node.id} [{node.node_type}] → {verdict}")
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

    Args:
        raw_post_id: raw_posts 表主键
        session:     异步数据库 Session

    Returns:
        dict with keys: raw_post_id, nodes_verified, skipped
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

    # ── 加载该帖子的所有节点 ───────────────────────────────────────────────
    nodes = list(
        (await session.exec(
            select(Node).where(Node.raw_post_id == raw_post_id)
        )).all()
    )

    nodes_verified = 0
    for node in nodes:
        if node.verdict is not None:
            continue

        verify_fn = VERIFIABLE_TYPES.get((node.domain, node.node_type))
        if verify_fn is None:
            continue

        changed = await verify_fn(node, session)
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


async def _search(query: str) -> str:
    """Tavily 搜索，返回格式化文本。"""
    results = await web_search(query, max_results=4)
    if results:
        return f"\n\n## 搜索结果\n\n{format_search_results(results)}"
    return ""


async def _call_llm(system: str, user: str) -> dict | None:
    resp = await chat_completion(system=system, user=user, max_tokens=_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


def _build_query(statement: str) -> str:
    base = statement[:200]
    current_year = str(datetime.date.today().year)
    if not re.search(r"20\d{2}", base):
        base = f"{base} {current_year}"
    return base


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
