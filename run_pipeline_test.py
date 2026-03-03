"""
全链路端到端测试脚本（v2 — 六实体架构）
========================================
输出：Fact / Conclusion / Prediction / Assumption / ImplicitCondition /
      Solution / Logic / Verdict / Quality / Stats 的所有字段。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

C_CYAN  = "\033[1;36m"
C_GREEN = "\033[1;32m"
C_WARN  = "\033[1;33m"
C_RED   = "\033[1;31m"
C_DIM   = "\033[2m"
C_RESET = "\033[0m"

def _h(t):    return f"{C_CYAN}{t}{C_RESET}"
def _ok(t):   return f"{C_GREEN}{t}{C_RESET}"
def _warn(t): return f"{C_WARN}{t}{C_RESET}"
def _dim(t):  return f"{C_DIM}{t}{C_RESET}"

def _sep(title=""):
    line = "─" * 64
    if title:
        print(f"\n{_h(line)}\n{_h(f'  {title}')}\n{_h(line)}")
    else:
        print(_h(line))

def _row(label, value, indent=4):
    pad = " " * indent
    label_str = f"{pad}{label:<30}"
    if value is None:
        print(f"{label_str}{_dim('—')}")
    else:
        print(f"{label_str}{value}")

def _block(title, text, indent=4):
    pad = " " * indent
    print(f"{pad}{C_CYAN}{title}{C_RESET}")
    if text:
        for line in str(text).splitlines():
            print(f"{pad}  {line}")
    else:
        print(f"{pad}  {_dim('—')}")

def _sub(title, indent=4):
    pad = " " * indent
    print(f"\n{pad}{C_CYAN}{title}{C_RESET}")

def _verdict_color(v):
    s = str(v)
    if "confirmed" in s or "validated" in s:
        return _ok(s)
    elif "refuted" in s or "false" in s:
        return _warn(s)
    elif "true" in s:
        return _ok(s)
    elif "uncertain" in s or "partial" in s:
        return _warn(s)
    else:
        return _dim(s)

def _align_color(v):
    if v == "true":     return _ok(v)
    if v == "false":    return _warn(v)
    if v == "uncertain": return _warn(v)
    return _dim(str(v) if v else "—")


async def run():
    # ── 清理旧数据库 ──────────────────────────────────────────────────────
    db_path = "test_anchor.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print(_dim("  (已清理旧测试数据库，本次全新运行)"))

    from anchor.database.session import create_tables, AsyncSessionLocal
    from anchor.collector.input_handler import parse_url, process_url
    from anchor.classifier.extractor import Extractor
    from anchor.tracker.author_profiler import AuthorProfiler
    from anchor.tracker.author_stats_updater import AuthorStatsUpdater
    from anchor.tracker.logic_verifier import LogicVerifier
    from anchor.tracker.reality_aligner import RealityAligner
    from anchor.tracker.prediction_monitor import PredictionMonitor
    from anchor.tracker.post_quality_evaluator import PostQualityEvaluator
    from anchor.tracker.role_evaluator import RoleEvaluator
    from anchor.tracker.solution_simulator import SolutionSimulator
    from anchor.tracker.verdict_deriver import VerdictDeriver
    from anchor.models import (
        Assumption, Author, AuthorStats, Conclusion, ConclusionVerdict,
        Fact, ImplicitCondition, Logic, MonitoredSource, PostQualityAssessment,
        Prediction, PredictionVerdict, RawPost, Solution, SolutionAssessment,
        Topic, VerificationReference,
    )
    from sqlmodel import select

    TARGET_URL = sys.argv[1] if len(sys.argv) > 1 else "https://weibo.com/1182426800/Qt28K6uTe"

    await create_tables()
    print(_ok("✓ 数据库初始化完成"))

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 1 — 采集
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 1 — 采集与入库")

    async with AsyncSessionLocal() as session:
        result = await process_url(TARGET_URL, session)

    src  = result.monitored_source
    auth = result.author
    posts = result.raw_posts

    print(_ok(f"✓ MonitoredSource  id={src.id}  platform={src.platform}  type={src.source_type}"))
    print(_ok(f"✓ Author           id={auth.id}  name={auth.name}  platform_id={auth.platform_id}"))
    print(_ok(f"✓ RawPost 入库数量 : {len(posts)}"))

    _parsed = parse_url(TARGET_URL)
    async with AsyncSessionLocal() as session:
        rp_row = (await session.exec(
            select(RawPost).where(
                RawPost.source == _parsed.platform,
                RawPost.external_id == _parsed.platform_id,
            )
        )).first()
        if not rp_row:
            rp_row = (await session.exec(
                select(RawPost).where(RawPost.source == _parsed.platform)
                .order_by(RawPost.id.desc())
            )).first()

    if not rp_row:
        print(_warn("  ⚠ 无 RawPost 入库（采集失败，请检查 Layer 1 日志）"))
        return

    print()
    _row("external_id",  rp_row.external_id)
    _row("source",       rp_row.source)
    _row("author_name",  rp_row.author_name)
    _row("posted_at",    rp_row.posted_at)
    _row("collected_at", rp_row.collected_at)
    _row("url",          rp_row.url)
    if rp_row.raw_metadata:
        meta = json.loads(rp_row.raw_metadata)
        for key in ("likes", "retweets", "replies", "reposts", "comments", "followers"):
            val = meta.get(key)
            if val is not None:
                _row(key, val)

    print()
    _block("【原始内容（完整版）】", rp_row.content)

    if rp_row.media_json:
        try:
            media_items = json.loads(rp_row.media_json)
        except Exception:
            media_items = []
        if media_items:
            print()
            print(f"    {C_CYAN}【媒体内容】{C_RESET}")
            for idx, item in enumerate(media_items, 1):
                mtype = item.get("type", "unknown")
                murl  = item.get("url", "—")
                print(f"      [{idx}] {mtype}: {murl[:100]}")

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 2 — 观点提取（v2 六实体）
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 2 — 观点提取（v2 六实体）")

    async with AsyncSessionLocal() as session:
        rp = await session.get(RawPost, rp_row.id)
        extractor = Extractor()
        extraction = await extractor.extract(rp, session)

    if extraction is None:
        print(_warn("  extraction=None（帖子可能已处理过）"))
    else:
        mark = _ok("✓") if extraction.is_relevant_content else _warn("⊘")
        print(f"  {mark} is_relevant_content : {extraction.is_relevant_content}")
        if not extraction.is_relevant_content:
            _row("skip_reason", extraction.skip_reason)
        if extraction.extraction_notes:
            print(_warn(f"  ⚠ extraction_notes : {extraction.extraction_notes}"))

    async with AsyncSessionLocal() as session:
        rp_check   = await session.get(RawPost, rp_row.id)
        facts      = list((await session.exec(select(Fact))).all())
        concls     = list((await session.exec(select(Conclusion))).all())
        preds      = list((await session.exec(select(Prediction))).all())
        assumps    = list((await session.exec(select(Assumption))).all())
        sols       = list((await session.exec(select(Solution))).all())
        logics     = list((await session.exec(select(Logic))).all())
        ics_layer2 = list((await session.exec(
            select(ImplicitCondition).where(ImplicitCondition.fact_id.in_([f.id for f in facts]))
        )).all()) if facts else []

    inf_count   = sum(1 for l in logics if l.logic_type == "inference")
    pred_count  = sum(1 for l in logics if l.logic_type == "prediction")
    deriv_count = sum(1 for l in logics if l.logic_type == "derivation")

    print(_ok(f"\n  ✓ RawPost.is_processed={rp_check.is_processed}  "
              f"processed_at={rp_check.processed_at}"))
    print(f"\n  Fact={len(facts)}  Conclusion={len(concls)}  Prediction={len(preds)}  "
          f"Assumption={len(assumps)}  ImplicitCondition={len(ics_layer2)}  Solution={len(sols)}  "
          f"Logic={len(logics)}（inf={inf_count} pred={pred_count} deriv={deriv_count}）")

    # ── Facts ─────────────────────────────────────────────────────────────
    _sub("【Facts】", indent=2)
    for f in facts:
        print(f"\n  {C_CYAN}[Fact id={f.id}]{C_RESET}")
        _row("claim",                  f.claim)
        _row("verifiable_statement",   f.verifiable_statement)
        _row("temporal_type",          f.temporal_type)
        _row("temporal_note",          f.temporal_note)
        _row("canonical_claim",        f.canonical_claim)
        _row("verifiable_expression",  f.verifiable_expression)
        _row("is_verifiable",          f.is_verifiable)

    # ── Conclusions ────────────────────────────────────────────────────────
    _sub("【Conclusions (回顾型)】", indent=2)
    for c in concls:
        print(f"\n  {C_CYAN}[Conclusion id={c.id}]{C_RESET}")
        _row("claim",                c.claim)
        _row("verifiable_statement", c.verifiable_statement)
        _row("temporal_type",        c.temporal_type)
        _row("author_confidence",    c.author_confidence)

    # ── Predictions ────────────────────────────────────────────────────────
    _sub("【Predictions (预测型)】", indent=2)
    if not preds:
        print(f"  {_dim('（无预测）')}")
    for p in preds:
        print(f"\n  {C_CYAN}[Prediction id={p.id}]{C_RESET}")
        _row("claim",                p.claim)
        _row("verifiable_statement", p.verifiable_statement)
        _row("temporal_note",        p.temporal_note)
        _row("author_confidence",    p.author_confidence)

    # ── Assumptions ────────────────────────────────────────────────────────
    _sub("【Assumptions (假设条件)】", indent=2)
    if not assumps:
        print(f"  {_dim('（无假设条件）')}")
    for a in assumps:
        print(f"\n  {C_CYAN}[Assumption id={a.id}]{C_RESET}")
        _row("condition_text",       a.condition_text)
        _row("verifiable_statement", a.verifiable_statement)
        _row("temporal_type",        a.temporal_type)
        _row("is_verifiable",        a.is_verifiable)

    # ── ImplicitConditions (Layer2) ────────────────────────────────────────
    _sub("【ImplicitConditions (Layer2提取)】", indent=2)
    if not ics_layer2:
        print(f"  {_dim('（无隐含条件）')}")
    for ic in ics_layer2:
        parent = f"Fact#{ic.fact_id}" if ic.fact_id else f"Conclusion#{ic.conclusion_id}"
        print(f"\n  {C_CYAN}[ImplicitCondition id={ic.id}  {parent}]{C_RESET}")
        _row("condition_text",       ic.condition_text)
        _row("verifiable_statement", ic.verifiable_statement)
        _row("is_consensus",         ic.is_consensus)

    # ── Solutions ──────────────────────────────────────────────────────────
    _sub("【Solutions】", indent=2)
    for s in sols:
        print(f"\n  {C_CYAN}[Solution id={s.id}]{C_RESET}")
        _row("claim",           s.claim)
        _row("action_type",     s.action_type)
        _row("action_target",   s.action_target)

    # ── Logics (含 chain_summary) ──────────────────────────────────────────
    _sub("【Logics (含 chain_summary)】", indent=2)
    for l in logics:
        if l.logic_type == "inference":
            target = f"Conclusion#{l.conclusion_id}"
        elif l.logic_type == "prediction":
            target = f"Prediction#{l.prediction_id}"
        else:
            target = f"Solution#{l.solution_id}"
        print(f"\n  {C_CYAN}[Logic id={l.id}  {l.logic_type} → {target}]{C_RESET}")
        _row("chain_type",      l.chain_type)
        _block("chain_summary", l.chain_summary)

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 0: 作者档案分析
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 0: 作者档案分析")

    author_profiler = AuthorProfiler()
    from anchor.config import settings as _settings_pre
    _web_mode_pre = "✓ 联网模式（Tavily）" if _settings_pre.tavily_api_key else "✗ 仅训练知识（未配置 TAVILY_API_KEY）"
    print(f"  查询模式: {_web_mode_pre}")

    async with AsyncSessionLocal() as session:
        a = await session.get(Author, auth.id)
        await author_profiler.profile(a, session)
        tier = a.credibility_tier
        tier_labels = {1: "顶级权威", 2: "行业专家", 3: "知名评论员", 4: "普通媒体/KOL", 5: "未知"}
        tier_str = f"Tier{tier} ({tier_labels.get(tier, '?')})" if tier else "—"
        tier_color = _ok(tier_str) if tier and tier <= 2 else (_warn(tier_str) if tier == 3 else _dim(tier_str))
        print(f"\n  {C_CYAN}[Author id={a.id}  name={a.name}]{C_RESET}")
        _row("role",            a.role)
        _row("expertise_areas", a.expertise_areas)
        _row("credibility_tier", tier_color)
        _row("profile_note",    a.profile_note)
        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 1: 逻辑链验证（LogicVerifier）
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 1: 逻辑链验证（LogicVerifier）")

    logic_verifier = LogicVerifier()
    print(f"  待验证逻辑总数: {len(logics)}")

    async with AsyncSessionLocal() as session:
        for logic in logics:
            l = await session.get(Logic, logic.id)
            print(f"\n  {C_CYAN}{'═'*58}{C_RESET}")
            print(f"  {C_CYAN}[Logic id={l.id}  {l.logic_type}]{C_RESET}")
            _block("chain_summary", l.chain_summary)
            await logic_verifier.verify(l, session)
            lv = l.logic_validity or "—"
            lv_color = (_ok(lv) if lv == "valid" else
                        (_warn(lv) if lv == "partial" else
                         (_warn(lv) if lv == "invalid" else _dim(lv))))
            _row("logic_validity", lv_color, indent=4)
            if l.logic_issues:
                try:
                    issues = json.loads(l.logic_issues)
                    if issues:
                        print(f"    {C_CYAN}逻辑问题:{C_RESET}")
                        for issue in issues:
                            print(f"      • {issue}")
                except Exception:
                    _row("logic_issues", l.logic_issues, indent=4)
        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 2: 现实对齐（RealityAligner）
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 2: 现实对齐（RealityAligner）")

    aligner = RealityAligner()
    from anchor.config import settings as _settings
    _web_mode = "✓ 联网模式（Tavily）" if _settings.tavily_api_key else "✗ 仅训练知识"
    print(f"  核查模式: {_web_mode}")
    print(f"  待对齐：{len(facts)} 事实，{len(concls)} 结论，{len(assumps)} 假设，{len(ics_layer2)} 隐含条件")

    async with AsyncSessionLocal() as session:
        # Facts
        for fact in facts:
            f = await session.get(Fact, fact.id)
            await aligner.align_fact(f, session)
            print(f"\n  {C_CYAN}[Fact id={f.id}]{C_RESET}  claim={f.claim[:50]}")
            _row("alignment_result",   _align_color(f.alignment_result), indent=4)
            _row("alignment_tier",     f"Tier{f.alignment_tier}" if f.alignment_tier else "—", indent=4)
            _block("alignment_evidence", f.alignment_evidence, indent=4)

        # Conclusions
        for conc in concls:
            c = await session.get(Conclusion, conc.id)
            await aligner.align_conclusion(c, session)
            print(f"\n  {C_CYAN}[Conclusion id={c.id}]{C_RESET}  claim={c.claim[:50]}")
            _row("alignment_result",   _align_color(c.alignment_result), indent=4)

        # Assumptions
        for assump in assumps:
            a_obj = await session.get(Assumption, assump.id)
            await aligner.align_assumption(a_obj, session)
            print(f"\n  {C_CYAN}[Assumption id={a_obj.id}]{C_RESET}  {a_obj.condition_text[:50]}")
            _row("alignment_result",   _align_color(a_obj.alignment_result), indent=4)

        # ImplicitConditions
        for ic in ics_layer2:
            ic_obj = await session.get(ImplicitCondition, ic.id)
            await aligner.align_implicit_condition(ic_obj, session)
            print(f"\n  {C_CYAN}[IC id={ic_obj.id}]{C_RESET}  {ic_obj.condition_text[:50]}")
            _row("alignment_result",   _align_color(ic_obj.alignment_result), indent=4)

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 3: 预测监控配置（PredictionMonitor）
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 3: 预测监控配置（PredictionMonitor）")

    pred_monitor = PredictionMonitor()
    print(f"  预测总数: {len(preds)}")

    if not preds:
        print(f"  {_dim('（本次内容无预测）')}")
    else:
        async with AsyncSessionLocal() as session:
            for pred in preds:
                p = await session.get(Prediction, pred.id)
                print(f"\n  {C_CYAN}[Prediction id={p.id}]{C_RESET}")
                _row("claim",              p.claim)
                _row("temporal_note",      p.temporal_note)

                await pred_monitor.setup(p, session)

                cond_status = p.conditional_monitoring_status or "not_applicable"
                cond_color = (_dim if cond_status == "not_applicable" else
                              (_warn if cond_status in ("abandoned", "waiting") else _ok))
                _row("conditional_status",      cond_color(cond_status), indent=4)
                if cond_status not in ("not_applicable",):
                    prob = p.assumption_probability or "—"
                    prob_color = _ok if prob == "high" else (_warn if prob == "medium" else _dim)
                    _row("conditional_assumption",  p.conditional_assumption,  indent=4)
                    _row("assumption_probability",  prob_color(prob),          indent=4)
                _row("monitoring_source_org",   p.monitoring_source_org,   indent=4)
                _row("monitoring_period_note",  p.monitoring_period_note,  indent=4)
                _row("monitoring_start",        p.monitoring_start,        indent=4)
                _row("monitoring_end",          p.monitoring_end,          indent=4)

            await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 4: 解决方案模拟 + 监控配置
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 4: 解决方案模拟")

    solution_simulator = SolutionSimulator()
    print(f"  待模拟解决方案总数: {len(sols)}")

    if not sols:
        print(f"  {_dim('（本次内容无解决方案）')}")
    else:
        async with AsyncSessionLocal() as session:
            for sol in sols:
                s = await session.get(Solution, sol.id)
                print(f"\n  {C_CYAN}{'═'*58}{C_RESET}")
                print(f"  {C_CYAN}[Solution id={s.id}]{C_RESET}")
                _row("claim",           s.claim)
                _row("action_type",     s.action_type)
                _row("action_target",   s.action_target)

                await solution_simulator.simulate(s, session)

                _sub("模拟执行结果:", indent=4)
                _block("simulated_action_note", s.simulated_action_note, indent=6)
                _row("monitoring_period_note",  s.monitoring_period_note, indent=6)
                _row("monitoring_start",        s.monitoring_start,       indent=6)
                _row("monitoring_end",          s.monitoring_end,         indent=6)
                if s.baseline_value:
                    _sub("基准价格（发布时刻）:", indent=4)
                    _row("baseline_metric",      s.baseline_metric,      indent=6)
                    _row("baseline_value",       _ok(s.baseline_value),  indent=6)

            await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 5: 裁定推导（VerdictDeriver）
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 5: 裁定推导")

    deriver = VerdictDeriver()

    async with AsyncSessionLocal() as session:
        # Conclusion 裁定
        for conc in concls:
            c = await session.get(Conclusion, conc.id)
            cv = await deriver.derive_conclusion(c, session)
            if cv:
                print(f"\n  {C_CYAN}[ConclusionVerdict id={cv.id} → Conclusion#{conc.id}]{C_RESET}")
                _row("verdict",    _verdict_color(cv.verdict))
                _block("claim",    c.claim)
                if cv.logic_trace:
                    try:
                        trace = json.loads(cv.logic_trace)
                        sup_align = trace.get("supporting_alignments", {})
                        if sup_align:
                            print(f"    {C_CYAN}支撑事实对齐:{C_RESET}")
                            for fid, ar in sup_align.items():
                                print(f"      Fact#{fid} → {_align_color(ar)}")
                        _row("logic_validity", trace.get("logic_validity"), indent=4)
                    except Exception:
                        pass
            else:
                print(f"\n  {C_CYAN}[Conclusion#{conc.id}]{C_RESET} {_dim('→ pending')}")

        # Prediction 裁定
        for pred in preds:
            p = await session.get(Prediction, pred.id)
            pv = await deriver.derive_prediction(p, session)
            if pv:
                print(f"\n  {C_CYAN}[PredictionVerdict id={pv.id} → Prediction#{pred.id}]{C_RESET}")
                _row("verdict",   _verdict_color(pv.verdict))
                _block("claim",   p.claim)
            else:
                print(f"\n  {C_CYAN}[Prediction#{pred.id}]{C_RESET} {_dim('→ pending（监控期未到）')}")

        # Solution 裁定
        for sol in sols:
            s = await session.get(Solution, sol.id)
            sa = await deriver.derive_solution(s, session)
            if sa:
                print(f"\n  {C_CYAN}[SolutionAssessment id={sa.id} → Solution#{sol.id}]{C_RESET}")
                _row("verdict",        _verdict_color(sa.verdict))
                _block("solution_claim", s.claim)
                _block("evidence_text",  sa.evidence_text)
            else:
                print(f"\n  {C_CYAN}[Solution#{sol.id}]{C_RESET} {_dim('→ pending')}")

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 6: 角色匹配评估
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 6: 角色匹配评估")

    role_evaluator = RoleEvaluator()

    async with AsyncSessionLocal() as session:
        a = await session.get(Author, auth.id)
        all_verdicts    = list((await session.exec(select(ConclusionVerdict))).all())
        all_assessments = list((await session.exec(select(SolutionAssessment))).all())

        for verdict in all_verdicts:
            c = await session.get(Conclusion, verdict.conclusion_id)
            if c is None:
                continue
            await role_evaluator.evaluate_conclusion_verdict(verdict, c, a, session)
            fit = verdict.role_fit or "—"
            fit_color = _ok if fit == "appropriate" else (_warn if fit == "questionable" else _dim)
            print(f"\n  {C_CYAN}[ConclusionVerdict id={verdict.id} → Conclusion#{c.id}]{C_RESET}")
            _row("role_fit",      fit_color(fit))
            _row("role_fit_note", verdict.role_fit_note)

        for assessment in all_assessments:
            s = await session.get(Solution, assessment.solution_id)
            if s is None:
                continue
            await role_evaluator.evaluate_solution_assessment(assessment, s, a, session)
            fit = assessment.role_fit or "—"
            fit_color = _ok if fit == "appropriate" else (_warn if fit == "questionable" else _dim)
            print(f"\n  {C_CYAN}[SolutionAssessment id={assessment.id} → Solution#{s.id}]{C_RESET}")
            _row("role_fit",      fit_color(fit))
            _row("role_fit_note", assessment.role_fit_note)

        if not all_verdicts and not all_assessments:
            print(f"  {_dim('（无已裁定的结论/解决方案，跳过角色评估）')}")

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 7: 内容质量评估
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 7: 内容质量评估（独特性 + 有效性）")

    post_quality_evaluator = PostQualityEvaluator()

    async with AsyncSessionLocal() as session:
        rp = await session.get(RawPost, rp_row.id)
        a  = await session.get(Author, auth.id)

        print(f"\n  {C_CYAN}[RawPost id={rp.id}  author={a.name}]{C_RESET}")
        await post_quality_evaluator.assess(rp, a, session)

        pqa_r = await session.exec(
            select(PostQualityAssessment).where(PostQualityAssessment.raw_post_id == rp.id)
        )
        pqa = pqa_r.first()
        if pqa:
            u_color = _ok if (pqa.uniqueness_score or 0) >= 0.7 else _warn
            e_color = _ok if (pqa.effectiveness_score or 0) >= 0.7 else _warn

            _sub("内容独特性:", indent=2)
            _row("uniqueness_score",    u_color(f"{pqa.uniqueness_score:.2f}" if pqa.uniqueness_score is not None else "—"), indent=4)
            _row("is_first_mover",      _ok("是") if pqa.is_first_mover else _dim("否"), indent=4)
            _row("similar_claim_count", pqa.similar_claim_count,  indent=4)
            _row("uniqueness_note",     pqa.uniqueness_note,      indent=4)

            _sub("内容有效性:", indent=2)
            _row("effectiveness_score", e_color(f"{pqa.effectiveness_score:.2f}" if pqa.effectiveness_score is not None else "—"), indent=4)
            _row("noise_ratio",         f"{pqa.noise_ratio:.2f}" if pqa.noise_ratio is not None else "—", indent=4)
            if pqa.noise_types:
                try:
                    ntypes = json.loads(pqa.noise_types)
                    _row("noise_types", "、".join(ntypes) if ntypes else "无", indent=4)
                except Exception:
                    _row("noise_types", pqa.noise_types, indent=4)
            else:
                _row("noise_types", _dim("无"), indent=4)
        else:
            print(f"  {_warn('质量评估失败或跳过')}")

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — Step 8: 作者综合统计更新
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 8: 作者综合统计更新")

    author_stats_updater = AuthorStatsUpdater()

    async with AsyncSessionLocal() as session:
        a = await session.get(Author, auth.id)
        await author_stats_updater.update(a, session)

        stats_r = await session.exec(
            select(AuthorStats).where(AuthorStats.author_id == auth.id)
        )
        stats = stats_r.first()
        if stats:
            def _fmt_rate(v, n):
                if v is None:
                    return _dim("N/A（样本=0）")
                color = _ok if v >= 0.7 else _warn
                return color(f"{v:.1%}") + _dim(f"（样本={n}）")

            overall = stats.overall_credibility_score
            overall_str = (
                _ok(f"{overall:.1f}/100") if overall and overall >= 70
                else (_warn(f"{overall:.1f}/100") if overall else _dim("N/A"))
            )

            print(f"\n  {C_CYAN}[AuthorStats  author={a.name}]{C_RESET}")
            _row("综合评分",     overall_str, indent=4)
            _row("分析内容数",   stats.total_posts_analyzed, indent=4)
            print()
            _row("① 事实准确率",    _fmt_rate(stats.fact_accuracy_rate,              stats.fact_accuracy_sample),             indent=4)
            _row("② 结论准确性",    _fmt_rate(stats.conclusion_accuracy_rate,         stats.conclusion_accuracy_sample),        indent=4)
            _row("③ 预测准确性",    _fmt_rate(stats.prediction_accuracy_rate,         stats.prediction_accuracy_sample),        indent=4)
            _row("④ 逻辑严谨性",    _fmt_rate(stats.logic_rigor_score,               stats.logic_rigor_sample),               indent=4)
            _row("⑤ 建议可靠性",    _fmt_rate(stats.recommendation_reliability_rate,  stats.recommendation_reliability_sample), indent=4)
            _row("⑥ 内容独特性",    _fmt_rate(stats.content_uniqueness_score,         stats.content_uniqueness_sample),         indent=4)
            _row("⑦ 内容有效性",    _fmt_rate(stats.content_effectiveness_score,      stats.content_effectiveness_sample),      indent=4)
        else:
            print(f"  {_warn('统计更新失败')}")

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # 最终数据库快照
    # ══════════════════════════════════════════════════════════════════════
    _sep("最终数据库快照")
    async with AsyncSessionLocal() as session:
        counts = {
            "authors":                  len(list((await session.exec(select(Author))).all())),
            "monitored_sources":        len(list((await session.exec(select(MonitoredSource))).all())),
            "raw_posts":                len(list((await session.exec(select(RawPost))).all())),
            "topics":                   len(list((await session.exec(select(Topic))).all())),
            "facts":                    len(list((await session.exec(select(Fact))).all())),
            "assumptions":              len(list((await session.exec(select(Assumption))).all())),
            "implicit_conditions":      len(list((await session.exec(select(ImplicitCondition))).all())),
            "conclusions":              len(list((await session.exec(select(Conclusion))).all())),
            "predictions":              len(list((await session.exec(select(Prediction))).all())),
            "solutions":                len(list((await session.exec(select(Solution))).all())),
            "logics":                   len(list((await session.exec(select(Logic))).all())),
            "conclusion_verdicts":      len(list((await session.exec(select(ConclusionVerdict))).all())),
            "prediction_verdicts":      len(list((await session.exec(select(PredictionVerdict))).all())),
            "solution_assessments":     len(list((await session.exec(select(SolutionAssessment))).all())),
            "post_quality_assessments": len(list((await session.exec(select(PostQualityAssessment))).all())),
            "author_stats":             len(list((await session.exec(select(AuthorStats))).all())),
        }

    print(f"  {'表名':<32} {'行数':>5}")
    print(f"  {'─' * 40}")
    for table, n in counts.items():
        ind = _ok("✓") if n > 0 else " "
        print(f"  {ind} {table:<30} {n:>5}")

    print()
    _sep()
    print(_ok("  全链路测试完成！（v2 六实体架构）"))
    _sep()


if __name__ == "__main__":
    asyncio.run(run())
