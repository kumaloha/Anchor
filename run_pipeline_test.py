"""
全链路端到端测试脚本
====================
四类模型完整输出：
  Layer 1 — 原始内容 + 元数据
  Layer 2 — Fact / Conclusion / Solution / Logic 所有属性
  Layer 3 — 九步流水线：
    Step 0:   作者档案分析
    Step 1:   事实核查（含 evidence_tier 分级）
    Step 2+3: 逻辑评估（完备性 + 一句话总结）
    Step 4a:  预测型结论监控配置
    Step 4b:  解决方案模拟 + 监控配置
    Step 5:   逻辑关系映射
    Step 6:   裁定推导（Conclusion 两类型 + Solution）
    Step 7:   角色匹配评估
    Step 8:   内容质量评估（独特性 + 有效性）
    Step 9:   作者综合统计更新
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
    label_str = f"{pad}{label:<28}"
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
    from anchor.tracker.condition_verifier import ConditionVerifier
    from anchor.tracker.logic_evaluator import LogicEvaluator
    from anchor.tracker.logic_relation_mapper import LogicRelationMapper
    from anchor.tracker.conclusion_monitor import ConclusionMonitor
    from anchor.tracker.post_quality_evaluator import PostQualityEvaluator
    from anchor.tracker.role_evaluator import RoleEvaluator
    from anchor.tracker.solution_simulator import SolutionSimulator
    from anchor.tracker.verdict_deriver import VerdictDeriver
    from anchor.models import (
        Author, AuthorStats, Conclusion, ConclusionVerdict, Fact, FactEvaluation,
        Logic, LogicRelation, MonitoredSource, PostQualityAssessment, RawPost,
        Solution, SolutionAssessment, Topic, VerificationReference,
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

    # 媒体信息
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
    # LAYER 2 — 观点提取
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 2 — 观点提取")

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
        rp_check = await session.get(RawPost, rp_row.id)
        facts   = list((await session.exec(select(Fact))).all())
        concls  = list((await session.exec(select(Conclusion))).all())
        sols    = list((await session.exec(select(Solution))).all())
        logics  = list((await session.exec(select(Logic))).all())

    retro_count = sum(1 for c in concls if c.conclusion_type == "retrospective")
    pred_count  = sum(1 for c in concls if c.conclusion_type == "predictive")
    inf_count   = sum(1 for l in logics if l.logic_type == "inference")
    deriv_count = sum(1 for l in logics if l.logic_type == "derivation")

    print(_ok(f"\n  ✓ RawPost.is_processed={rp_check.is_processed}  "
              f"processed_at={rp_check.processed_at}"))
    print(f"\n  Fact={len(facts)}  "
          f"Conclusion={len(concls)}（回顾={retro_count} 预测={pred_count}）  "
          f"Solution={len(sols)}  "
          f"Logic={len(logics)}（inference={inf_count} derivation={deriv_count}）")

    # ── Facts ─────────────────────────────────────────────────────────────
    print()
    for f in facts:
        status_color = _ok if str(f.status) == "verified_true" else (
            _warn if str(f.status) == "pending" else _dim
        )
        print(f"  {C_CYAN}[Fact id={f.id}  status={status_color(f.status)}]{C_RESET}")
        _row("claim",                  f.claim)
        _row("canonical_claim",        f.canonical_claim)
        _row("verifiable_expression",  f.verifiable_expression)
        _row("is_verifiable",          f.is_verifiable)
        _row("validity_start_note",    f.validity_start_note)
        _row("validity_end_note",      f.validity_end_note)

        async with AsyncSessionLocal() as session:
            refs = list((await session.exec(
                select(VerificationReference).where(VerificationReference.fact_id == f.id)
            )).all())
        if refs:
            print(f"    {C_CYAN}引用来源 ({len(refs)} 条):{C_RESET}")
            for r in refs:
                print(f"      [{r.organization}] {r.data_description}")
                if r.url:
                    print(f"        url: {r.url}")
        print()

    # ── Conclusions ────────────────────────────────────────────────────────
    for c in concls:
        type_label = _ok("[回顾型]") if c.conclusion_type == "retrospective" else _warn("[预测型]")
        print(f"  {C_CYAN}[Conclusion id={c.id}]{C_RESET} {type_label}")
        _row("claim",              c.claim)
        _row("canonical_claim",    c.canonical_claim)
        _row("conclusion_type",    c.conclusion_type)
        _row("time_horizon_note",  c.time_horizon_note)
        if c.conclusion_type == "predictive":
            _row("valid_until",    c.valid_until)
        _row("status",             c.status)
        print()

    # ── Solutions ──────────────────────────────────────────────────────────
    for s in sols:
        print(f"  {C_CYAN}[Solution id={s.id}]{C_RESET}")
        _row("claim",              s.claim)
        _row("action_type",        s.action_type)
        _row("action_target",      s.action_target)
        _row("action_rationale",   s.action_rationale)
        _row("status",             s.status)
        print()

    # ── Logics ─────────────────────────────────────────────────────────────
    for l in logics:
        if l.logic_type == "inference":
            target = f"Conclusion#{l.conclusion_id}"
            supporting = json.loads(l.supporting_fact_ids or "[]")
            assumptions = json.loads(l.assumption_fact_ids or "[]")
            print(f"  {C_CYAN}[Logic id={l.id}  inference → {target}]{C_RESET}")
            _row("supporting_facts",   f"Fact IDs {supporting}")
            _row("assumption_facts",   f"Fact IDs {assumptions}")
        else:
            source_concs = json.loads(l.source_conclusion_ids or "[]")
            print(f"  {C_CYAN}[Logic id={l.id}  derivation → Solution#{l.solution_id}]{C_RESET}")
            _row("source_conclusions", f"Conclusion IDs {source_concs}")
        _row("logic_completeness", _dim("（Layer3 填写）"))
        print()

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
        print(f"\n  {C_CYAN}[Author id={a.id}  name={a.name}  platform={a.platform}]{C_RESET}")
        _row("description",    a.description)
        _row("profile_url",    a.profile_url)

        await author_profiler.profile(a, session)

        _row("role",            a.role, indent=4)
        _row("expertise_areas", a.expertise_areas, indent=4)
        _row("known_biases",    a.known_biases, indent=4)
        tier = a.credibility_tier
        tier_labels = {1: "顶级权威", 2: "行业专家", 3: "知名评论员", 4: "普通媒体/KOL", 5: "未知"}
        tier_str = f"Tier{tier} ({tier_labels.get(tier, '?')})" if tier else "—"
        tier_color = _ok(tier_str) if tier and tier <= 2 else (_warn(tier_str) if tier == 3 else _dim(tier_str))
        _row("credibility_tier", tier_color, indent=4)
        _row("profile_note",    a.profile_note, indent=4)

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # LAYER 3 — 六步流水线
    # ══════════════════════════════════════════════════════════════════════
    _sep("LAYER 3 — Step 1: 事实核查（含 evidence_tier）")

    verifier = ConditionVerifier()
    from anchor.config import settings as _settings
    _web_mode = "✓ 联网模式（Tavily）" if _settings.tavily_api_key else "✗ 仅训练知识（未配置 TAVILY_API_KEY）"
    print(f"  核查模式: {_web_mode}")
    print(f"  待核查事实总数: {len(facts)}")

    async with AsyncSessionLocal() as session:
        for fact in facts:
            f = await session.get(Fact, fact.id)
            print(f"\n  {C_CYAN}[Fact id={f.id}]{C_RESET}")
            _row("claim",                 f.claim)
            _row("canonical_claim",       f.canonical_claim)
            _row("is_verifiable",         f.is_verifiable)

            if f.is_verifiable:
                fe = await verifier.verify(f, session)
                if fe:
                    r_color = (_ok if str(fe.result) == "true"
                               else (_warn if str(fe.result) in ("false",) else
                                     (_warn if str(fe.result) == "uncertain" else _dim)))
                    # evidence_tier 颜色
                    tier_str = f"Tier{fe.evidence_tier}" if fe.evidence_tier else "—"
                    tier_color = (_ok(tier_str) if fe.evidence_tier == 1
                                  else (_warn(tier_str) if fe.evidence_tier == 2
                                        else _dim(tier_str)))
                    print(f"\n    {C_CYAN}核查结果:{C_RESET}")
                    _row("result",          r_color(fe.result), indent=6)
                    _row("evidence_tier",   tier_color, indent=6)
                    _row("evidence_text",   fe.evidence_text, indent=6)
                    _row("evaluator_notes", fe.evaluator_notes, indent=6)
                    # 展示 authoritative_links（从 fact.verified_source_data 解析）
                    links = []
                    if f.verified_source_data:
                        try:
                            links = json.loads(f.verified_source_data)
                        except Exception:
                            pass
                    if links:
                        print(f"      {C_CYAN}权威来源链接:{C_RESET}")
                        for lk in links:
                            print(f"        [{lk.get('org', '—')}]")
                            print(f"          url : {lk.get('url', '—')}")
                            if lk.get('description'):
                                print(f"          desc: {lk['description']}")
                    else:
                        _row("authoritative_links", None, indent=6)
                else:
                    print(f"    {_warn('核查调用失败')}")
            else:
                print(f"\n    {_dim('→ is_verifiable=False，跳过核查')}")

        await session.commit()

    # ── Step 2+3: 逻辑评估 ────────────────────────────────────────────────
    _sep("LAYER 3 — Step 2+3: 逻辑评估")

    logic_evaluator = LogicEvaluator()
    print(f"  待评估逻辑总数: {len(logics)}")

    async with AsyncSessionLocal() as session:
        for logic in logics:
            l = await session.get(Logic, logic.id)
            if l.logic_type == "inference":
                target = f"Conclusion#{l.conclusion_id}"
            else:
                target = f"Solution#{l.solution_id}"
            print(f"\n  {C_CYAN}[Logic id={l.id}  {l.logic_type} → {target}]{C_RESET}")

            await logic_evaluator.evaluate(l, session)

            lc_color = _ok if str(l.logic_completeness) in ("complete", "LogicCompleteness.COMPLETE") else _warn
            _row("logic_completeness",    lc_color(l.logic_completeness) if l.logic_completeness else _dim("—"), indent=4)
            _row("logic_note",            l.logic_note, indent=4)
            _row("one_sentence_summary",  l.one_sentence_summary, indent=4)

        await session.commit()

    # ── Step 4a: 预测型结论监控配置 ───────────────────────────────────────
    _sep("LAYER 3 — Step 4a: 预测型结论监控配置")

    conclusion_monitor = ConclusionMonitor()
    pred_concls = [c for c in concls if c.conclusion_type == "predictive"]
    print(f"  预测型结论总数: {len(pred_concls)}")

    if not pred_concls:
        print(f"  {_dim('（本次内容无预测型结论）')}")
    else:
        async with AsyncSessionLocal() as session:
            for conc in pred_concls:
                c = await session.get(Conclusion, conc.id)
                print(f"\n  {C_CYAN}[Conclusion id={c.id}  predictive]{C_RESET}")
                _row("claim",              c.claim)

                await conclusion_monitor.setup(c, session)

                _row("monitoring_source_org",  c.monitoring_source_org, indent=4)
                _row("monitoring_source_url",  c.monitoring_source_url, indent=4)
                _row("monitoring_period_note", c.monitoring_period_note, indent=4)
                _row("monitoring_start",       c.monitoring_start, indent=4)
                _row("monitoring_end",         c.monitoring_end, indent=4)

            await session.commit()

    # ── Step 4b: 解决方案模拟 + 监控配置 ─────────────────────────────────
    _sep("LAYER 3 — Step 4b: 解决方案模拟")

    solution_simulator = SolutionSimulator()
    print(f"  待模拟解决方案总数: {len(sols)}")

    if not sols:
        print(f"  {_dim('（本次内容无解决方案）')}")
    else:
        async with AsyncSessionLocal() as session:
            for sol in sols:
                s = await session.get(Solution, sol.id)
                print(f"\n  {C_CYAN}[Solution id={s.id}]{C_RESET}")
                _row("claim",          s.claim)
                _row("action_type",    s.action_type)
                _row("action_target",  s.action_target)

                await solution_simulator.simulate(s, session)

                _row("simulated_action_note",  s.simulated_action_note, indent=4)
                _row("monitoring_source_org",  s.monitoring_source_org, indent=4)
                _row("monitoring_source_url",  s.monitoring_source_url, indent=4)
                _row("monitoring_period_note", s.monitoring_period_note, indent=4)
                _row("monitoring_start",       s.monitoring_start, indent=4)
                _row("monitoring_end",         s.monitoring_end, indent=4)

            await session.commit()

    # ── Step 5: 逻辑关系映射 ───────────────────────────────────────────────
    _sep("LAYER 3 — Step 5: 逻辑关系映射")

    relation_mapper = LogicRelationMapper()
    print(f"  分析 {len(logics)} 条逻辑之间的支撑关系…")

    async with AsyncSessionLocal() as session:
        fresh_logics = list((await session.exec(select(Logic))).all())
        relations = await relation_mapper.map(fresh_logics, session)
        await session.commit()

    if not relations:
        print(f"  {_dim('未发现逻辑间支撑关系')}")
    else:
        for rel in relations:
            rtype = rel.relation_type
            rcolor = _ok if rtype == "supports" else (_warn if rtype == "contextualizes" else _dim)
            arrow = f"L{rel.from_logic_id} --{rcolor(rtype)}--> L{rel.to_logic_id}"
            print(f"\n  {C_CYAN}{arrow}{C_RESET}")
            _row("note", rel.note, indent=4)

    # ── Step 6: 裁定推导 ───────────────────────────────────────────────────
    _sep("LAYER 3 — Step 6: 裁定推导")

    deriver = VerdictDeriver()

    async with AsyncSessionLocal() as session:
        # Conclusion 裁定
        for conc in concls:
            c = await session.get(Conclusion, conc.id)
            cv = await deriver.derive_conclusion(c, session)
            if cv:
                v_color = _ok if str(cv.verdict) == "confirmed" else _warn
                type_label = "[回顾型]" if c.conclusion_type == "retrospective" else "[预测型]"
                print(f"\n  {C_CYAN}[ConclusionVerdict id={cv.id}  {type_label} → Conclusion#{conc.id}]{C_RESET}")
                _row("verdict", v_color(cv.verdict))
                if cv.logic_trace:
                    trace = json.loads(cv.logic_trace)
                    _row("supporting_facts", trace.get("supporting_facts"))
                    _row("assumption_facts", trace.get("assumption_facts"))
            else:
                type_label = "[回顾型]" if conc.conclusion_type == "retrospective" else "[预测型]"
                print(f"\n  {C_CYAN}[Conclusion#{conc.id} {type_label}]{C_RESET} "
                      f"{_dim('→ pending（监控期未到或无逻辑）')}")

        # Solution 裁定
        for sol in sols:
            s = await session.get(Solution, sol.id)
            sa = await deriver.derive_solution(s, session)
            if sa:
                v_color = _ok if str(sa.verdict) == "validated" else _warn
                print(f"\n  {C_CYAN}[SolutionAssessment id={sa.id}  → Solution#{sol.id}]{C_RESET}")
                _row("verdict", v_color(sa.verdict))
                _row("evidence_text", sa.evidence_text)
            else:
                print(f"\n  {C_CYAN}[Solution#{sol.id}]{C_RESET} "
                      f"{_dim('→ pending（监控期未到）')}")

        await session.commit()

    # ── Step 7: 角色匹配评估 ───────────────────────────────────────────────
    _sep("LAYER 3 — Step 7: 角色匹配评估")

    role_evaluator = RoleEvaluator()

    async with AsyncSessionLocal() as session:
        # 加载作者（带档案）
        a = await session.get(Author, auth.id)
        role_label = a.role or "未知角色"
        tier = a.credibility_tier or 5
        print(f"\n  作者: {a.name}  |  角色: {role_label}  |  可信度 Tier{tier}")

        # Conclusion 角色匹配
        all_verdicts = list((await session.exec(select(ConclusionVerdict))).all())
        for verdict in all_verdicts:
            c = await session.get(Conclusion, verdict.conclusion_id)
            if c is None:
                continue

            await role_evaluator.evaluate_conclusion_verdict(verdict, c, a, session)

            fit = verdict.role_fit or "—"
            fit_color = _ok if fit == "appropriate" else (_warn if fit == "questionable" else _dim)
            type_label = "[回顾型]" if c.conclusion_type == "retrospective" else "[预测型]"
            print(f"\n  {C_CYAN}[ConclusionVerdict id={verdict.id}  {type_label} → Conclusion#{c.id}]{C_RESET}")
            _row("claim",        c.claim[:80] + ("…" if len(c.claim) > 80 else ""), indent=4)
            _row("verdict",      str(verdict.verdict), indent=4)
            _row("role_fit",     fit_color(fit), indent=4)
            _row("role_fit_note", verdict.role_fit_note, indent=4)

        # Solution 角色匹配
        all_assessments = list((await session.exec(select(SolutionAssessment))).all())
        for assessment in all_assessments:
            s = await session.get(Solution, assessment.solution_id)
            if s is None:
                continue

            await role_evaluator.evaluate_solution_assessment(assessment, s, a, session)

            fit = assessment.role_fit or "—"
            fit_color = _ok if fit == "appropriate" else (_warn if fit == "questionable" else _dim)
            print(f"\n  {C_CYAN}[SolutionAssessment id={assessment.id}  → Solution#{s.id}]{C_RESET}")
            _row("claim",        s.claim[:80] + ("…" if len(s.claim) > 80 else ""), indent=4)
            _row("verdict",      str(assessment.verdict), indent=4)
            _row("role_fit",     fit_color(fit), indent=4)
            _row("role_fit_note", assessment.role_fit_note, indent=4)

        if not all_verdicts and not all_assessments:
            print(f"  {_dim('（无已裁定的结论/解决方案，跳过角色评估）')}")

        await session.commit()

    # ── Step 8: 内容质量评估 ───────────────────────────────────────────────
    _sep("LAYER 3 — Step 8: 内容质量评估（独特性 + 有效性）")

    post_quality_evaluator = PostQualityEvaluator()

    async with AsyncSessionLocal() as session:
        rp = await session.get(RawPost, rp_row.id)
        a = await session.get(Author, auth.id)

        print(f"\n  {C_CYAN}[RawPost id={rp.id}  author={a.name}]{C_RESET}")
        _row("url", rp.url)

        await post_quality_evaluator.assess(rp, a, session)

        pqa_r = await session.exec(
            select(PostQualityAssessment).where(
                PostQualityAssessment.raw_post_id == rp.id
            )
        )
        pqa = pqa_r.first()
        if pqa:
            u_color = _ok if (pqa.uniqueness_score or 0) >= 0.7 else _warn
            e_color = _ok if (pqa.effectiveness_score or 0) >= 0.7 else _warn
            print(f"\n  {C_CYAN}内容独特性:{C_RESET}")
            _row("uniqueness_score",   u_color(f"{pqa.uniqueness_score:.2f}" if pqa.uniqueness_score is not None else "—"), indent=4)
            _row("is_first_mover",     _ok("是") if pqa.is_first_mover else _dim("否"), indent=4)
            _row("similar_claim_count",  pqa.similar_claim_count, indent=4)
            _row("similar_author_count", pqa.similar_author_count, indent=4)
            _row("uniqueness_note",    pqa.uniqueness_note, indent=4)
            print(f"\n  {C_CYAN}内容有效性:{C_RESET}")
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
            _row("effectiveness_note", pqa.effectiveness_note, indent=4)
        else:
            print(f"  {_warn('质量评估失败或跳过')}")

        await session.commit()

    # ── Step 9: 作者综合统计更新 ───────────────────────────────────────────
    _sep("LAYER 3 — Step 9: 作者综合统计更新")

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
                    return _dim(f"N/A（样本=0）")
                color = _ok if v >= 0.7 else _warn
                return color(f"{v:.1%}") + _dim(f"（样本={n}）")

            overall = stats.overall_credibility_score
            overall_str = (
                _ok(f"{overall:.1f}/100") if overall and overall >= 70
                else (_warn(f"{overall:.1f}/100") if overall else _dim("N/A"))
            )

            print(f"\n  {C_CYAN}[AuthorStats  author={a.name}]{C_RESET}")
            _row("综合评分",    overall_str, indent=4)
            _row("分析内容数",  stats.total_posts_analyzed, indent=4)
            print()
            _row("① 事实准确率",    _fmt_rate(stats.fact_accuracy_rate, stats.fact_accuracy_sample), indent=4)
            _row("② 结论准确性",    _fmt_rate(stats.conclusion_accuracy_rate, stats.conclusion_accuracy_sample), indent=4)
            _row("③ 预测准确性",    _fmt_rate(stats.prediction_accuracy_rate, stats.prediction_accuracy_sample), indent=4)
            _row("④ 逻辑严谨性",    _fmt_rate(stats.logic_rigor_score, stats.logic_rigor_sample), indent=4)
            _row("⑤ 建议可靠性",    _fmt_rate(stats.recommendation_reliability_rate, stats.recommendation_reliability_sample), indent=4)
            _row("⑥ 内容独特性",    _fmt_rate(stats.content_uniqueness_score, stats.content_uniqueness_sample), indent=4)
            _row("⑦ 内容有效性",    _fmt_rate(stats.content_effectiveness_score, stats.content_effectiveness_sample), indent=4)
        else:
            print(f"  {_warn('统计更新失败')}")

        await session.commit()

    # ══════════════════════════════════════════════════════════════════════
    # 数据库快照
    # ══════════════════════════════════════════════════════════════════════
    _sep("最终数据库快照")
    async with AsyncSessionLocal() as session:
        counts = {
            "authors":               len(list((await session.exec(select(Author))).all())),
            "monitored_sources":     len(list((await session.exec(select(MonitoredSource))).all())),
            "raw_posts":             len(list((await session.exec(select(RawPost))).all())),
            "topics":                len(list((await session.exec(select(Topic))).all())),
            "facts":                 len(list((await session.exec(select(Fact))).all())),
            "verification_refs":     len(list((await session.exec(select(VerificationReference))).all())),
            "conclusions":           len(list((await session.exec(select(Conclusion))).all())),
            "solutions":             len(list((await session.exec(select(Solution))).all())),
            "logics":                len(list((await session.exec(select(Logic))).all())),
            "fact_evaluations":      len(list((await session.exec(select(FactEvaluation))).all())),
            "logic_relations":       len(list((await session.exec(select(LogicRelation))).all())),
            "conclusion_verdicts":   len(list((await session.exec(select(ConclusionVerdict))).all())),
            "solution_assessments":  len(list((await session.exec(select(SolutionAssessment))).all())),
            "post_quality_assessments": len(list((await session.exec(select(PostQualityAssessment))).all())),
            "author_stats":          len(list((await session.exec(select(AuthorStats))).all())),
        }

    print(f"  {'表名':<30} {'行数':>5}")
    print(f"  {'─' * 37}")
    for table, n in counts.items():
        ind = _ok("✓") if n > 0 else " "
        print(f"  {ind} {table:<28} {n:>5}")

    print()
    _sep()
    print(_ok("  全链路测试完成！"))
    _sep()


if __name__ == "__main__":
    asyncio.run(run())
