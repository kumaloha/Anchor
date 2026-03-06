"""
Chain 3 — 验证链路
==================
输入：raw_post_id
输出：六实体的 verdict 字段写入 DB

5个子步骤（各自独立可跳过）：

Step 1 — 验证 Facts → fact_verdict
  Tavily 搜索 + LLM 判定：credible|vague|unreliable|unavailable

Step 2 — 评估 Assumptions → assumption_verdict
  Tavily + LLM：high_probability|medium_probability|low_probability|unavailable

Step 3 — 检查 ImplicitConditions → implicit_verdict
  is_obvious_consensus=True 直接写 consensus 跳过 LLM
  否则：Tavily + LLM：consensus|contested|false

Step 4 — 推导 Conclusion verdict（规则推导，读 Relationship 表）：
  all facts credible/vague + no low_probability assumption → confirmed
  any fact unreliable OR any low_probability assumption   → refuted
  mix credible+unavailable OR contested implicit          → partial
  all facts unavailable                                   → unverifiable

Step 5 — 监控 Predictions → prediction_verdict：
  temporal_validity=no_timeframe → 标记 invalid，跳过
  monitoring_end 未到           → pending，跳过
  monitoring_end 已过           → Tavily + LLM 确认

用法：
  async with AsyncSessionLocal() as session:
      result = await run_chain3(raw_post_id=1, session=session)
"""

from __future__ import annotations

import datetime
import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Assumption,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    PolicyItem,
    PolicyTheme,
    Prediction,
    RawPost,
    _utcnow,
)
from anchor.verify.web_searcher import format_search_results, web_search

_MAX_TOKENS = 1024

_SYS_POLICY_EXEC = """\
你是政策执行追踪分析师。给定一条政策承诺及相关搜索结果，判断该政策的当前执行情况。

execution_status 枚举：
  implemented   — 已完全落地，有明确数据或公告证明
  in_progress   — 正在推进，已有具体行动但尚未完成
  stalled       — 推进受阻或明显低于预期，有证据表明延迟/缩水
  not_started   — 尚无任何落地迹象，仍停留在承诺阶段
  unknown       — 搜索结果不足以判断

输出 JSON：
{"execution_status": "...", "execution_note": "≤80字，说明执行进展并注明来源依据"}
严格输出 JSON，不加任何其他文字。\
"""

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYS_FACT = """\
你是专业事实核查员。给定一条事实陈述，结合搜索结果和训练知识，从两个维度判断：

【维度一】来源可信度（source_tier）：
  authoritative  — 政府官网、央行、国际机构、学术机构原始数据
  mainstream_media — 路透社/彭博/BBC/WSJ/FT等主流财经媒体报道
  market_data    — 股价、债券收益率、大宗商品价格等实时可核查市场数据
  rumor          — 匿名消息源、未经证实的传言
  no_source      — 没有任何可追溯公开来源

【维度二】数据精确度（is_vague）：
  false — 具体：有明确数字、日期、名称等可核实指标
  true  — 宽泛：使用"大约"、"约"、"估计"等模糊表达

输出 JSON：{"source_tier": "...", "is_vague": false, "evidence": "...", "confidence": "high|medium|low"}
严格输出 JSON，不加任何其他文字。"""

_SYS_ASSUMPTION = """\
你是专业分析员。给定一条假设条件陈述（"如果X则Y"类型的前提），结合搜索结果和训练知识，
评估该假设条件成立的概率等级。

输出 JSON：
{"assumption_verdict": "high_probability|medium_probability|low_probability|unavailable",
 "evidence": "...", "confidence": "high|medium|low"}

分类标准：
  high_probability   — 该假设条件很可能成立（有充分历史先例或现有强烈迹象支持）
  medium_probability — 该假设条件有一定可能性成立（有部分迹象但不确定）
  low_probability    — 该假设条件成立概率较低（缺乏支撑或与现实趋势相悖）
  unavailable        — 无法判断（证据不足或超出知识范围）

严格输出 JSON，不加任何其他文字。"""

_SYS_IMPLICIT = """\
你是专业分析员。给定一条隐含条件陈述（推理中未说出但依赖的暗含前提），
结合搜索结果和训练知识，评估其是否为普遍共识。

输出 JSON：
{"implicit_verdict": "consensus|contested|false",
 "evidence": "...", "confidence": "high|medium|low"}

分类标准：
  consensus  — 该前提是学术、行业或社会公认的共识，无需额外论证
  contested  — 该前提存在明显的对立观点或争议，不是普遍共识
  false      — 该前提明显与事实或共识相悖，是错误的暗含假设

严格输出 JSON，不加任何其他文字。"""

_SYS_PREDICTION = """\
你是专业预测核查员。给定一条预测型陈述及其时间范围，
结合搜索结果和训练知识，对预测结果进行分类。

输出 JSON：
{"prediction_verdict": "pending|accurate|directional|off_target|wrong",
 "evidence": "...", "confidence": "high|medium|low"}

分类标准：
  pending     — 预测时间窗口尚未到达，无法判断
  accurate    — 预测方向和量级均与现实吻合
  directional — 方向一致，但精度不足以定性为"准确"
  off_target  — 方向正确但量级偏差明显，或时间范围偏差较大
  wrong       — 预测方向与现实相反，或被明确证伪

严格输出 JSON，不加任何其他文字。"""

# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def run_chain3(raw_post_id: int, session: AsyncSession) -> dict:
    """执行链路3：对某帖子的所有实体进行验证

    Args:
        raw_post_id: raw_posts 表主键
        session:     异步数据库 Session

    Returns:
        dict with keys:
          raw_post_id,
          facts_verified, assumptions_verified, implicit_verified,
          conclusions_derived, predictions_checked
    """
    logger.info(f"[Chain3] Starting verification for raw_post_id={raw_post_id}")

    # ── 政策模式：追踪各政策条目执行情况 ────────────────────────────────
    policy_items = list(
        (await session.exec(
            select(PolicyItem).where(PolicyItem.raw_post_id == raw_post_id)
        )).all()
    )
    items_tracked = 0
    if policy_items:
        logger.info(f"[Chain3] Policy post detected: {len(policy_items)} items → tracking execution")
        items_tracked = await _track_policy_execution(raw_post_id, policy_items, session)

    # ── 加载该帖子的所有实体 ─────────────────────────────────────────────
    facts = list(
        (await session.exec(select(Fact).where(Fact.raw_post_id == raw_post_id))).all()
    )
    assumptions = list(
        (await session.exec(
            select(Assumption).where(Assumption.raw_post_id == raw_post_id)
        )).all()
    )
    implicit_conditions = list(
        (await session.exec(
            select(ImplicitCondition).where(ImplicitCondition.raw_post_id == raw_post_id)
        )).all()
    )
    conclusions = list(
        (await session.exec(
            select(Conclusion).where(Conclusion.raw_post_id == raw_post_id)
        )).all()
    )
    predictions = list(
        (await session.exec(
            select(Prediction).where(Prediction.raw_post_id == raw_post_id)
        )).all()
    )

    # ── Step 1：验证 Facts ───────────────────────────────────────────────
    facts_verified = 0
    for fact in facts:
        if fact.fact_verdict is not None:
            continue
        changed = await _verify_fact(fact, session)
        if changed:
            facts_verified += 1
    await session.flush()

    # ── Step 2：评估 Assumptions ─────────────────────────────────────────
    assumptions_verified = 0
    for assumption in assumptions:
        if assumption.assumption_verdict is not None:
            continue
        changed = await _verify_assumption(assumption, session)
        if changed:
            assumptions_verified += 1
    await session.flush()

    # ── Step 3：检查 ImplicitConditions ──────────────────────────────────
    implicit_verified = 0
    for ic in implicit_conditions:
        if ic.implicit_verdict is not None:
            continue
        changed = await _verify_implicit(ic, session)
        if changed:
            implicit_verified += 1
    await session.flush()

    # ── Step 4：推导 Conclusion verdict ──────────────────────────────────
    conclusions_derived = 0
    for conclusion in conclusions:
        if conclusion.is_in_cycle:
            continue
        if conclusion.conclusion_verdict is not None:
            continue
        changed = await _derive_conclusion_verdict(conclusion, session, raw_post_id)
        if changed:
            conclusions_derived += 1
    await session.flush()

    # ── Step 5：监控 Predictions ──────────────────────────────────────────
    predictions_checked = 0
    for pred in predictions:
        if pred.prediction_verdict not in (None, "pending"):
            continue
        changed = await _check_prediction(pred, session)
        if changed:
            predictions_checked += 1
    await session.flush()

    await session.commit()

    logger.info(
        f"[Chain3] Done for raw_post_id={raw_post_id}: "
        f"items_tracked={items_tracked}, "
        f"facts={facts_verified}, assumptions={assumptions_verified}, "
        f"implicit={implicit_verified}, conclusions={conclusions_derived}, "
        f"predictions={predictions_checked}"
    )

    return {
        "raw_post_id": raw_post_id,
        "items_tracked": items_tracked,
        "facts_verified": facts_verified,
        "assumptions_verified": assumptions_verified,
        "implicit_verified": implicit_verified,
        "conclusions_derived": conclusions_derived,
        "predictions_checked": predictions_checked,
    }


# ---------------------------------------------------------------------------
# Step 1 — Fact 验证
# ---------------------------------------------------------------------------


async def _verify_fact(fact: Fact, session: AsyncSession) -> bool:
    stmt = fact.verifiable_statement or fact.claim
    query = _build_query(stmt, fact.temporal_note)
    search_text = await _search(query)

    prompt = f"""请对以下事实陈述进行分类核查：

## 待核查陈述
{stmt}{f'{chr(10)}时间范围：{fact.temporal_note}' if fact.temporal_note else ''}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_FACT, prompt)
    if result is None:
        return False

    source_tier = _normalize(result.get("source_tier"),
                             {"authoritative", "mainstream_media", "market_data", "rumor", "no_source"},
                             "no_source")
    is_vague = bool(result.get("is_vague", False))

    if source_tier == "no_source":
        verdict = "unavailable"
    elif source_tier == "rumor":
        verdict = "unreliable"
    elif is_vague:
        verdict = "vague"
    else:
        verdict = "credible"

    fact.fact_verdict = verdict
    fact.verdict_evidence = _safe_str(result.get("evidence"))
    fact.verdict_verified_at = _utcnow()
    session.add(fact)

    logger.info(f"[Chain3] Fact id={fact.id} → source={source_tier} vague={is_vague} → {verdict}")
    return True


# ---------------------------------------------------------------------------
# Step 2 — Assumption 验证
# ---------------------------------------------------------------------------


async def _verify_assumption(assumption: Assumption, session: AsyncSession) -> bool:
    stmt = assumption.verifiable_statement or assumption.condition_text
    query = _build_query(stmt, assumption.temporal_note)
    search_text = await _search(query)

    prompt = f"""请评估以下假设条件成立的概率：

## 假设条件
{stmt}{f'{chr(10)}时间范围：{assumption.temporal_note}' if assumption.temporal_note else ''}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_ASSUMPTION, prompt)
    if result is None:
        return False

    verdict = _normalize(result.get("assumption_verdict"),
                         {"high_probability", "medium_probability", "low_probability", "unavailable"},
                         "unavailable")

    assumption.assumption_verdict = verdict
    assumption.verdict_evidence = _safe_str(result.get("evidence"))
    assumption.verdict_verified_at = _utcnow()
    session.add(assumption)

    logger.info(f"[Chain3] Assumption id={assumption.id} → {verdict}")
    return True


# ---------------------------------------------------------------------------
# Step 3 — ImplicitCondition 验证
# ---------------------------------------------------------------------------


async def _verify_implicit(ic: ImplicitCondition, session: AsyncSession) -> bool:
    # 显而易见的共识：直接跳过 LLM
    if ic.is_obvious_consensus:
        ic.implicit_verdict = "consensus"
        ic.verdict_evidence = "显而易见的共识，无需验证"
        ic.verdict_verified_at = _utcnow()
        session.add(ic)
        logger.info(f"[Chain3] ImplicitCondition id={ic.id} → consensus (shortcut)")
        return True

    stmt = ic.condition_text
    query = _build_query(stmt, None)
    search_text = await _search(query)

    prompt = f"""请评估以下隐含条件是否为普遍共识：

## 隐含条件
{stmt}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_IMPLICIT, prompt)
    if result is None:
        return False

    verdict = _normalize(result.get("implicit_verdict"),
                         {"consensus", "contested", "false"},
                         "contested")

    ic.implicit_verdict = verdict
    ic.verdict_evidence = _safe_str(result.get("evidence"))
    ic.verdict_verified_at = _utcnow()
    session.add(ic)

    logger.info(f"[Chain3] ImplicitCondition id={ic.id} → {verdict}")
    return True


# ---------------------------------------------------------------------------
# Step 4 — Conclusion verdict 规则推导
# ---------------------------------------------------------------------------

# verdict 聚合规则
_FACT_CREDIBLE = {"credible", "vague"}
_FACT_UNRELIABLE = {"unreliable"}
_FACT_UNAVAILABLE = {"unavailable"}
_ASSUMP_REFUTE = {"low_probability"}
_IMPLICIT_CONTESTED = {"contested", "false"}


async def _derive_conclusion_verdict(
    conclusion: Conclusion, session: AsyncSession, raw_post_id: int
) -> bool:
    # 从 Relationship 表查询支撑边
    rels = list(
        (await session.exec(
            select(EntityRelationship).where(
                EntityRelationship.raw_post_id == raw_post_id,
                EntityRelationship.target_type == "conclusion",
                EntityRelationship.target_id == conclusion.id,
            )
        )).all()
    )

    fact_ids = [r.source_id for r in rels if r.source_type == "fact"]
    assumption_ids = [r.source_id for r in rels if r.source_type == "assumption"]
    implicit_ids = [r.source_id for r in rels if r.source_type == "implicit_condition"]

    # 加载各实体的 verdict
    fact_verdicts: list[str] = []
    if fact_ids:
        fs = list((await session.exec(select(Fact).where(Fact.id.in_(fact_ids)))).all())
        fact_verdicts = [f.fact_verdict or "unavailable" for f in fs]

    assumption_verdicts: list[str] = []
    if assumption_ids:
        asps = list(
            (await session.exec(
                select(Assumption).where(Assumption.id.in_(assumption_ids))
            )).all()
        )
        assumption_verdicts = [a.assumption_verdict or "unavailable" for a in asps]

    implicit_verdicts: list[str] = []
    if implicit_ids:
        ics = list(
            (await session.exec(
                select(ImplicitCondition).where(ImplicitCondition.id.in_(implicit_ids))
            )).all()
        )
        implicit_verdicts = [ic.implicit_verdict or "contested" for ic in ics]

    # ── 推导规则 ──────────────────────────────────────────────────────────
    trace = {
        "fact_verdicts": fact_verdicts,
        "assumption_verdicts": assumption_verdicts,
        "implicit_verdicts": implicit_verdicts,
    }

    # 任意 low_probability 假设 → refuted
    if any(v in _ASSUMP_REFUTE for v in assumption_verdicts):
        verdict = "refuted"
        trace["reason"] = "low_probability assumption"

    # 任意 unreliable 事实 → refuted
    elif any(v in _FACT_UNRELIABLE for v in fact_verdicts):
        verdict = "refuted"
        trace["reason"] = "unreliable fact"

    # 全部 unavailable 事实（无假设条件）→ unverifiable
    elif (
        fact_verdicts
        and all(v in _FACT_UNAVAILABLE for v in fact_verdicts)
        and not assumption_verdicts
        and not implicit_verdicts
    ):
        verdict = "unverifiable"
        trace["reason"] = "all facts unavailable"

    # 无任何支撑实体 → pending
    elif not fact_verdicts and not assumption_verdicts and not implicit_verdicts:
        verdict = "pending"
        trace["reason"] = "no supporting entities"

    else:
        has_credible = any(v in _FACT_CREDIBLE for v in fact_verdicts)
        has_unavailable = any(v in _FACT_UNAVAILABLE for v in fact_verdicts)
        has_contested_implicit = any(v in _IMPLICIT_CONTESTED for v in implicit_verdicts)

        if has_credible and not has_unavailable and not has_contested_implicit:
            verdict = "confirmed"
        elif has_credible and (has_unavailable or has_contested_implicit):
            verdict = "partial"
        else:
            verdict = "partial"

        trace["reason"] = "aggregated"

    conclusion.conclusion_verdict = verdict
    conclusion.verdict_trace = json.dumps(trace, ensure_ascii=False)
    session.add(conclusion)

    logger.info(f"[Chain3] Conclusion id={conclusion.id} → {verdict}")
    return True


# ---------------------------------------------------------------------------
# Step 5 — Prediction 监控
# ---------------------------------------------------------------------------


async def _check_prediction(pred: Prediction, session: AsyncSession) -> bool:
    # no_timeframe → 标记 invalid，跳过验证
    if pred.temporal_validity == "no_timeframe":
        if pred.prediction_verdict is None:
            pred.prediction_verdict = "pending"
            session.add(pred)
            logger.info(
                f"[Chain3] Prediction id={pred.id} has no_timeframe, set to pending"
            )
            return True
        return False

    now = _utcnow()

    # monitoring_end 未到 → pending
    if pred.monitoring_end and now < pred.monitoring_end:
        if pred.prediction_verdict is None:
            pred.prediction_verdict = "pending"
            session.add(pred)
        return pred.prediction_verdict is None

    # 无 monitoring_end 或已过期 → LLM 验证
    stmt = pred.claim
    query = _build_query(stmt, pred.temporal_note)
    search_text = await _search(query)

    temporal_ctx = f"\n时间范围：{pred.temporal_note}" if pred.temporal_note else ""
    prompt = f"""请对以下预测型陈述进行核查分类：

## 预测陈述
{stmt}{temporal_ctx}
{search_text}

请基于搜索结果（优先）和训练知识输出 JSON。"""

    result = await _call_llm(_SYS_PREDICTION, prompt)
    if result is None:
        return False

    verdict = _normalize(result.get("prediction_verdict"),
                         {"pending", "accurate", "directional", "off_target", "wrong"},
                         "pending")

    pred.prediction_verdict = verdict
    pred.verdict_evidence = _safe_str(result.get("evidence"))
    pred.verdict_verified_at = _utcnow()
    session.add(pred)

    logger.info(f"[Chain3] Prediction id={pred.id} → {verdict}")
    return True


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _search(query: str) -> str:
    """Tavily 搜索，返回格式化文本（无结果时返回空字符串）。"""
    results = await web_search(query, max_results=4)
    if results:
        return f"\n\n## 搜索结果\n\n{format_search_results(results)}"
    return ""


async def _call_llm(system: str, user: str) -> dict | None:
    resp = await chat_completion(system=system, user=user, max_tokens=_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


def _build_query(statement: str, temporal_note: str | None) -> str:
    base = statement[:200]
    # 拼接时间注释
    query = f"{base} {temporal_note}" if temporal_note else base
    # 如果查询中没有4位年份，自动追加当前年份，确保搜索命中正确时期
    current_year = str(datetime.date.today().year)
    if not re.search(r"20\d{2}", query):
        query = f"{query} {current_year}"
    return query


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
        logger.warning(f"[Chain3] JSON parse error: {exc}")
        return None


def _normalize(value: str | None, valid: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in valid else default


def _safe_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# ---------------------------------------------------------------------------
# 政策模式 — 追踪各政策条目执行情况
# ---------------------------------------------------------------------------


async def _track_policy_execution(
    raw_post_id: int,
    items: list[PolicyItem],
    session: AsyncSession,
) -> int:
    """对每条尚无 execution_status 的 PolicyItem，做 web 搜索并判断执行情况。

    优先追踪硬约束（is_hard_target=True）和强制类（urgency=mandatory）条目。

    Returns:
        成功追踪的条目数量
    """
    post = await session.get(RawPost, raw_post_id)
    year = post.posted_at.year if post and post.posted_at else datetime.date.today().year

    # 优先级：硬约束 > 强制 > 其余
    def priority(item: PolicyItem) -> int:
        if item.is_hard_target:
            return 0
        if item.urgency == "mandatory":
            return 1
        return 2

    tracked = 0
    for item in sorted(items, key=priority):
        if item.execution_status is not None:
            continue  # 已追踪，跳过

        metric_hint = f"（目标：{item.metric_value}）" if item.metric_value else ""
        query = f"{year}年 {item.summary}{metric_hint} 落实 进展"
        search_text = await _search(query)

        prompt = f"""政策年份：{year}年
政策承诺：{item.policy_text}
量化目标：{item.metric_value or '无'}
{search_text}

请判断该政策承诺的当前执行情况。"""

        result = await _call_llm(_SYS_POLICY_EXEC, prompt)
        if result:
            status = _normalize(
                result.get("execution_status"),
                {"implemented", "in_progress", "stalled", "not_started", "unknown"},
                "unknown",
            )
            item.execution_status = status
            item.execution_note = _safe_str(result.get("execution_note"))
            session.add(item)
            await session.flush()
            tracked += 1
            logger.info(
                f"[Chain3] PolicyItem id={item.id} [{item.summary}] → {status}"
            )

    return tracked
