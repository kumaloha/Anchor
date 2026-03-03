"""Anchor UI — 单文件 FastAPI Web 界面
=====================================
运行方式：python anchor_ui.py
访问：http://localhost:8765
"""

import os

# 必须在 anchor 模块导入前设置 DB（与测试脚本使用不同文件）
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")

import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))


# ── 数据库初始化 ──────────────────────────────────────────────────────────────

async def _init_db():
    from anchor.database.session import create_tables, engine
    from sqlalchemy import text
    await create_tables()
    # 迁移：为已存在的数据库补充新字段（忽略"column already exists"错误）
    async with engine.begin() as conn:
        migrations = [
            ("post_quality_assessments", "stance_label",    "TEXT"),
            ("post_quality_assessments", "stance_note",     "TEXT"),
            ("authors",                  "author_group_id", "INTEGER"),
            ("raw_posts",               "is_duplicate",     "INTEGER DEFAULT 0"),
            ("raw_posts",               "original_post_id", "INTEGER"),
            # v2 新字段：facts
            ("facts", "verifiable_statement", "TEXT"),
            ("facts", "temporal_type",        "TEXT DEFAULT 'retrospective'"),
            ("facts", "temporal_note",        "TEXT"),
            ("facts", "alignment_result",     "TEXT"),
            ("facts", "alignment_evidence",   "TEXT"),
            ("facts", "alignment_tier",       "INTEGER"),
            ("facts", "alignment_confidence", "TEXT"),
            ("facts", "alignment_verified_at","TEXT"),
            # v2 新字段：conclusions
            ("conclusions", "verifiable_statement", "TEXT"),
            ("conclusions", "temporal_type",        "TEXT DEFAULT 'retrospective'"),
            ("conclusions", "temporal_note",        "TEXT"),
            ("conclusions", "alignment_result",     "TEXT"),
            ("conclusions", "alignment_evidence",   "TEXT"),
            ("conclusions", "alignment_tier",       "INTEGER"),
            ("conclusions", "alignment_confidence", "TEXT"),
            ("conclusions", "alignment_verified_at","TEXT"),
            # v2 新字段：implicit_conditions
            ("implicit_conditions", "prediction_id",        "INTEGER"),
            ("implicit_conditions", "verifiable_statement", "TEXT"),
            ("implicit_conditions", "temporal_type",        "TEXT"),
            ("implicit_conditions", "temporal_note",        "TEXT"),
            ("implicit_conditions", "alignment_result",     "TEXT"),
            ("implicit_conditions", "alignment_evidence",   "TEXT"),
            ("implicit_conditions", "alignment_tier",       "INTEGER"),
            ("implicit_conditions", "alignment_confidence", "TEXT"),
            ("implicit_conditions", "alignment_verified_at","TEXT"),
            ("implicit_conditions", "is_consensus",         "INTEGER DEFAULT 0"),
            # v2 新字段：logics
            ("logics", "prediction_id",              "INTEGER"),
            ("logics", "source_prediction_ids",      "TEXT"),
            ("logics", "assumption_ids",             "TEXT"),
            ("logics", "layer2_implicit_condition_ids", "TEXT"),
            ("logics", "chain_summary",              "TEXT"),
            ("logics", "chain_type",                 "TEXT"),
            ("logics", "logic_validity",             "TEXT"),
            ("logics", "logic_issues",               "TEXT"),
            ("logics", "logic_verified_at",          "TEXT"),
        ]
        for table, col, typedef in migrations:
            try:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"
                ))
            except Exception:
                pass  # 列已存在则忽略


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    yield


app = FastAPI(lifespan=lifespan)

# 存储任务队列 {task_id: asyncio.Queue}
_tasks: dict[str, asyncio.Queue] = {}


# ── HTTP 端点 ─────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    url: str


class ReprofileRequest(BaseModel):
    author_id: int


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=_HTML)


@app.post("/reprofile")
async def reprofile(req: ReprofileRequest):
    """强制对某作者重新联网查询档案（修正 tier=5 或过时数据）。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import Author
    from anchor.tracker.author_profiler import AuthorProfiler
    async with AsyncSessionLocal() as session:
        author = await session.get(Author, req.author_id)
        if author is None:
            raise HTTPException(status_code=404, detail="Author not found")
        await AuthorProfiler().profile(author, session, force=True)
        await session.commit()
        return {
            "ok": True,
            "author_id": author.id,
            "name": author.name,
            "role": author.role,
            "credibility_tier": author.credibility_tier,
            "profile_note": author.profile_note,
        }


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    task_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _tasks[task_id] = queue
    asyncio.create_task(_run_pipeline(req.url, queue))
    return {"task_id": task_id}


@app.get("/stream/{task_id}")
async def stream(task_id: str):
    queue = _tasks.get(task_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async def generator() -> AsyncGenerator[str, None]:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            if event.get("type") in ("done", "error"):
                _tasks.pop(task_id, None)
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _emit(q: asyncio.Queue, event: dict):
    await q.put(event)


async def _run_pipeline(url: str, q: asyncio.Queue):
    try:
        from anchor.database.session import AsyncSessionLocal
        from anchor.collector.input_handler import parse_url, process_url
        from anchor.classifier.extractor import Extractor
        from anchor.tracker.author_profiler import AuthorProfiler
        from anchor.tracker.author_stats_updater import AuthorStatsUpdater
        from anchor.tracker.logic_verifier import LogicVerifier
        from anchor.tracker.reality_aligner import RealityAligner
        from anchor.tracker.prediction_monitor import PredictionMonitor
        from anchor.tracker.logic_relation_mapper import LogicRelationMapper
        from anchor.tracker.post_quality_evaluator import PostQualityEvaluator
        from anchor.tracker.role_evaluator import RoleEvaluator
        from anchor.tracker.solution_simulator import SolutionSimulator
        from anchor.tracker.verdict_deriver import VerdictDeriver
        from anchor.models import (
            Assumption, Author, AuthorGroup, AuthorStats, AuthorStanceProfile,
            Conclusion, ConclusionVerdict, Fact, FactEvaluation,
            ImplicitCondition, Logic, LogicRelation, MonitoredSource, PostQualityAssessment,
            Prediction, PredictionVerdict, RawPost, Solution, SolutionAssessment,
            Topic, VerificationReference,
        )
        from sqlmodel import select

        # ── Layer 1：采集 ──────────────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 1, "label": "Layer 1 — 采集内容"})

        async with AsyncSessionLocal() as session:
            result = await process_url(url, session)

        src = result.monitored_source
        auth = result.author

        _parsed = parse_url(url)
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
            await _emit(q, {"type": "error", "message": "采集失败：未找到 RawPost，请检查 URL 格式"})
            return

        raw_post_id = rp_row.id
        author_id = auth.id
        post_url = rp_row.url

        await _emit(q, {
            "type": "step_done", "num": 1,
            "detail": f"采集成功：{rp_row.author_name} | {rp_row.url}",
        })

        # ── Layer 2：观点提取 ──────────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 2, "label": "Layer 2 — 观点提取"})

        async with AsyncSessionLocal() as session:
            rp = await session.get(RawPost, raw_post_id)
            extractor = Extractor()
            extraction = await extractor.extract(rp, session)

        if extraction and not extraction.is_relevant_content:
            await _emit(q, {"type": "error", "message": f"内容不相关，跳过：{extraction.skip_reason}"})
            return

        async with AsyncSessionLocal() as session:
            facts = list((await session.exec(
                select(Fact).where(Fact.raw_post_id == raw_post_id)
            )).all())
            concls = list((await session.exec(
                select(Conclusion).where(Conclusion.source_url == post_url)
            )).all())
            preds = list((await session.exec(
                select(Prediction).where(Prediction.source_url == post_url)
            )).all())
            assumps = list((await session.exec(
                select(Assumption).where(Assumption.raw_post_id == raw_post_id)
            )).all())
            sols = list((await session.exec(
                select(Solution).where(Solution.source_url == post_url)
            )).all())
            conc_ids = [c.id for c in concls]
            pred_ids = [p.id for p in preds]
            sol_ids = [s.id for s in sols]
            fact_ids = [f.id for f in facts]
            all_logics = list((await session.exec(select(Logic))).all())
            logics = [
                l for l in all_logics
                if (l.logic_type == "inference" and l.conclusion_id in conc_ids)
                or (l.logic_type == "prediction" and l.prediction_id in pred_ids)
                or (l.logic_type == "derivation" and l.solution_id in sol_ids)
            ]

        await _emit(q, {
            "type": "step_done", "num": 2,
            "detail": (
                f"提取：{len(facts)} 事实，{len(concls)} 结论，"
                f"{len(preds)} 预测，{len(assumps)} 假设，{len(sols)} 解决方案"
            ),
        })

        # ── Step 0：作者档案 ───────────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 3, "label": "Step 0 — 作者档案分析"})

        author_profiler = AuthorProfiler()
        async with AsyncSessionLocal() as session:
            a = await session.get(Author, author_id)
            await author_profiler.profile(a, session)
            await session.commit()
            tier = a.credibility_tier or "?"

        await _emit(q, {"type": "step_done", "num": 3, "detail": f"作者：{a.name}，Tier{tier}"})

        # ── Step 1：逻辑验证（LogicVerifier）──────────────────────────────
        await _emit(q, {"type": "step", "num": 4, "label": f"Step 1 — 逻辑链验证（{len(logics)} 条）"})

        logic_verifier = LogicVerifier()
        async with AsyncSessionLocal() as session:
            for logic in logics:
                l = await session.get(Logic, logic.id)
                if l:
                    await logic_verifier.verify(l, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 4, "detail": "逻辑链验证完成"})

        # ── Step 2：现实对齐（RealityAligner）────────────────────────────
        n_align = len(facts) + len(concls)
        await _emit(q, {"type": "step", "num": 5, "label": f"Step 2 — 现实对齐（{n_align} 个实体）"})

        aligner = RealityAligner()
        async with AsyncSessionLocal() as session:
            for fact in facts:
                f = await session.get(Fact, fact.id)
                if f:
                    await aligner.align_fact(f, session)
            for conc in concls:
                c = await session.get(Conclusion, conc.id)
                if c:
                    await aligner.align_conclusion(c, session)
            # Also align implicit conditions and assumptions
            if fact_ids:
                all_ics = list((await session.exec(
                    select(ImplicitCondition).where(ImplicitCondition.fact_id.in_(fact_ids))
                )).all())
                for ic in all_ics:
                    ic_obj = await session.get(ImplicitCondition, ic.id)
                    if ic_obj:
                        await aligner.align_implicit_condition(ic_obj, session)
            for assump in assumps:
                a_obj = await session.get(Assumption, assump.id)
                if a_obj:
                    await aligner.align_assumption(a_obj, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 5, "detail": "现实对齐完成"})

        # ── Step 3：预测监控配置（PredictionMonitor）──────────────────────
        await _emit(q, {"type": "step", "num": 6, "label": f"Step 3 — 预测监控配置（{len(preds)} 条）"})

        pred_monitor = PredictionMonitor()
        if preds:
            async with AsyncSessionLocal() as session:
                for pred in preds:
                    p = await session.get(Prediction, pred.id)
                    if p:
                        await pred_monitor.setup(p, session)
                await session.commit()

        await _emit(q, {
            "type": "step_done", "num": 6,
            "detail": f"配置 {len(preds)} 个预测监控",
        })

        # ── Step 4：解决方案模拟 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 7, "label": "Step 4 — 解决方案模拟执行"})

        solution_simulator = SolutionSimulator()
        if sols:
            async with AsyncSessionLocal() as session:
                for sol in sols:
                    s = await session.get(Solution, sol.id)
                    if s:
                        await solution_simulator.simulate(s, session)
                await session.commit()

        await _emit(q, {"type": "step_done", "num": 7, "detail": f"模拟 {len(sols)} 个解决方案"})

        # ── Step 5：裁定推导（VerdictDeriver）────────────────────────────
        await _emit(q, {"type": "step", "num": 8, "label": "Step 5 — 裁定推导"})

        deriver = VerdictDeriver()
        async with AsyncSessionLocal() as session:
            for conc in concls:
                c = await session.get(Conclusion, conc.id)
                if c:
                    await deriver.derive_conclusion(c, session)
            for pred in preds:
                p = await session.get(Prediction, pred.id)
                if p:
                    await deriver.derive_prediction(p, session)
            for sol in sols:
                s = await session.get(Solution, sol.id)
                if s:
                    await deriver.derive_solution(s, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 8, "detail": "裁定完成"})

        # ── Step 6：角色匹配评估 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 9, "label": "Step 6 — 角色匹配评估"})

        role_evaluator = RoleEvaluator()
        async with AsyncSessionLocal() as session:
            a = await session.get(Author, author_id)
            all_verdicts = list((await session.exec(
                select(ConclusionVerdict).where(ConclusionVerdict.conclusion_id.in_(conc_ids))
            )).all()) if conc_ids else []
            all_assessments = list((await session.exec(
                select(SolutionAssessment).where(SolutionAssessment.solution_id.in_(sol_ids))
            )).all()) if sol_ids else []
            for verdict in all_verdicts:
                c = await session.get(Conclusion, verdict.conclusion_id)
                if c:
                    await role_evaluator.evaluate_conclusion_verdict(verdict, c, a, session)
            for assessment in all_assessments:
                s = await session.get(Solution, assessment.solution_id)
                if s:
                    await role_evaluator.evaluate_solution_assessment(assessment, s, a, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 9, "detail": "角色匹配评估完成"})

        # ── Step 7：内容质量评估 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 10, "label": "Step 7 — 内容质量评估"})

        post_quality_evaluator = PostQualityEvaluator()
        async with AsyncSessionLocal() as session:
            rp = await session.get(RawPost, raw_post_id)
            a = await session.get(Author, author_id)
            await post_quality_evaluator.assess(rp, a, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 10, "detail": "内容质量评估完成"})

        # ── Step 8：作者统计更新 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 11, "label": "Step 8 — 作者综合统计更新"})

        author_stats_updater = AuthorStatsUpdater()
        async with AsyncSessionLocal() as session:
            a = await session.get(Author, author_id)
            await author_stats_updater.update(a, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 11, "detail": "统计更新完成"})

        # ── 汇总结果 ──────────────────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 12, "label": "汇总结果"})
        data = await _collect_results(raw_post_id, author_id, post_url)
        await _emit(q, {"type": "done", "data": data})

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        await _emit(q, {"type": "error", "message": f"{exc}\n\n{tb}"})


# ── 结果收集 ──────────────────────────────────────────────────────────────────

async def _collect_results(raw_post_id: int, author_id: int, post_url: str) -> dict:
    """从数据库收集所有结果，构建结构化输出。只取本次分析的数据。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import (
        Assumption, Author, AuthorGroup, AuthorStats, AuthorStanceProfile,
        Conclusion, ConclusionVerdict, Fact, FactEvaluation,
        ImplicitCondition, Logic, PostQualityAssessment, Prediction, PredictionVerdict,
        RawPost, Solution, SolutionAssessment, VerificationReference,
    )
    from sqlmodel import select

    async with AsyncSessionLocal() as session:
        rp = await session.get(RawPost, raw_post_id)
        author = await session.get(Author, author_id)

        # ── Facts ────────────────────────────────────────────────────────
        all_facts = list((await session.exec(
            select(Fact).where(Fact.raw_post_id == raw_post_id)
        )).all())
        fact_ids = [f.id for f in all_facts]

        fact_evals: dict[int, FactEvaluation] = {}
        if fact_ids:
            for fe in (await session.exec(
                select(FactEvaluation).where(FactEvaluation.fact_id.in_(fact_ids))
            )).all():
                fact_evals[fe.fact_id] = fe

        ics_by_fact: dict[int, list] = {}
        if fact_ids:
            for ic in (await session.exec(
                select(ImplicitCondition).where(ImplicitCondition.fact_id.in_(fact_ids))
            )).all():
                ics_by_fact.setdefault(ic.fact_id, []).append(ic)

        facts_out = []
        for f in all_facts:
            fe = fact_evals.get(f.id)
            refs = list((await session.exec(
                select(VerificationReference).where(VerificationReference.fact_id == f.id)
            )).all())
            ics = ics_by_fact.get(f.id, [])
            facts_out.append({
                "id": f.id,
                "claim": f.claim,
                "canonical_claim": f.canonical_claim,
                "verifiable_expression": f.verifiable_expression,
                "is_verifiable": f.is_verifiable,
                "result": str(fe.result) if fe else "pending",
                "evidence_tier": fe.evidence_tier if fe else None,
                "evidence_summary": fe.evidence_text if fe else None,
                "evaluator_notes": fe.evaluator_notes if fe else None,
                "verification_method": f.verification_method,
                "validity_start_note": f.validity_start_note,
                "validity_end_note": f.validity_end_note,
                "refs": [
                    {"org": r.organization, "desc": r.data_description, "url": r.url}
                    for r in refs
                ],
                "implicit_conditions": [
                    {
                        "id": ic.id,
                        "condition_text": ic.condition_text,
                        "verification_result": ic.verification_result,
                        "verification_note": ic.verification_note,
                        "vote_consensus": ic.vote_consensus,
                        "vote_not_consensus": ic.vote_not_consensus,
                        "consensus_trend": ic.consensus_trend,
                        "consensus_trend_note": ic.consensus_trend_note,
                    }
                    for ic in ics
                ],
            })

        fact_map = {f["id"]: f for f in facts_out}

        # ── Conclusions & Solutions ───────────────────────────────────────
        all_concls = list((await session.exec(
            select(Conclusion).where(Conclusion.source_url == post_url)
        )).all())
        conc_ids = [c.id for c in all_concls]

        all_sols = list((await session.exec(
            select(Solution).where(Solution.source_url == post_url)
        )).all())
        sol_ids = [s.id for s in all_sols]

        # Predictions and Assumptions (v2)
        all_preds = list((await session.exec(
            select(Prediction).where(Prediction.source_url == post_url)
        )).all())
        pred_ids = [p.id for p in all_preds]

        all_assumps = list((await session.exec(
            select(Assumption).where(Assumption.raw_post_id == raw_post_id)
        )).all())

        # Logics for this analysis
        all_logics = list((await session.exec(select(Logic))).all())
        our_logics = [
            l for l in all_logics
            if (l.logic_type == "inference" and l.conclusion_id in conc_ids)
            or (l.logic_type == "prediction" and l.prediction_id in pred_ids)
            or (l.logic_type == "derivation" and l.solution_id in sol_ids)
        ]

        # conclusion_id → logic info
        conc_logic_map: dict[int, dict] = {}
        # prediction_id → logic info
        pred_logic_map: dict[int, dict] = {}
        # solution_id → source_conclusion_ids
        sol_source_map: dict[int, list[int]] = {}
        for l in our_logics:
            if l.logic_type == "inference" and l.conclusion_id:
                conc_logic_map[l.conclusion_id] = {
                    "supporting": json.loads(l.supporting_fact_ids or "[]"),
                    "assumptions": json.loads(l.assumption_fact_ids or "[]"),
                    "sup_concs": json.loads(l.supporting_conclusion_ids or "[]"),
                    "completeness": str(l.logic_completeness) if l.logic_completeness else None,
                    "logic_note": l.logic_note,
                    "summary": l.one_sentence_summary,
                    "chain_summary": l.chain_summary,
                    "logic_validity": l.logic_validity,
                }
            elif l.logic_type == "prediction" and l.prediction_id:
                pred_logic_map[l.prediction_id] = {
                    "supporting": json.loads(l.supporting_fact_ids or "[]"),
                    "assumptions": json.loads(l.assumption_fact_ids or "[]"),
                    "chain_summary": l.chain_summary,
                    "logic_validity": l.logic_validity,
                }
            elif l.logic_type == "derivation" and l.solution_id:
                sol_source_map[l.solution_id] = json.loads(l.source_conclusion_ids or "[]")

        # Solution assessments
        sol_assessments: dict[int, SolutionAssessment] = {}
        if sol_ids:
            for sa in (await session.exec(
                select(SolutionAssessment).where(SolutionAssessment.solution_id.in_(sol_ids))
            )).all():
                sol_assessments[sa.solution_id] = sa

        # Build solution data list, grouped by source conclusion
        conc_solutions: dict[int, list] = {}
        for sol in all_sols:
            sa = sol_assessments.get(sol.id)
            sol_data = {
                "id": sol.id,
                "claim": sol.claim,
                "action_type": sol.action_type,
                "action_target": sol.action_target,
                "action_rationale": sol.action_rationale,
                "verdict": str(sa.verdict) if sa else None,
                "evidence_text": sa.evidence_text if sa else None,
                "role_fit": sa.role_fit if sa else None,
                "role_fit_note": sa.role_fit_note if sa else None,
                "baseline_value": sol.baseline_value,
                "baseline_metric": sol.baseline_metric,
                "baseline_recorded_at": str(sol.baseline_recorded_at) if sol.baseline_recorded_at else None,
                "monitoring_period_note": sol.monitoring_period_note,
            }
            for cid in sol_source_map.get(sol.id, []):
                conc_solutions.setdefault(cid, []).append(sol_data)

        # Conclusion verdicts
        verdict_map: dict[int, ConclusionVerdict] = {}
        if conc_ids:
            for cv in (await session.exec(
                select(ConclusionVerdict).where(ConclusionVerdict.conclusion_id.in_(conc_ids))
            )).all():
                verdict_map[cv.conclusion_id] = cv

        # Build conclusion data list
        concls_out = []
        for c in all_concls:
            logic_info = conc_logic_map.get(c.id, {})
            supporting_fact_ids = logic_info.get("supporting", [])
            assumption_fact_ids = logic_info.get("assumptions", [])
            cv = verdict_map.get(c.id)

            supporting_facts = []
            for fid in supporting_fact_ids:
                fd = fact_map.get(fid)
                if fd:
                    supporting_facts.append({"role": "supporting", **fd})
            for fid in assumption_fact_ids:
                fd = fact_map.get(fid)
                if fd:
                    supporting_facts.append({"role": "assumption", **fd})

            logic_trace_parsed = None
            if cv and cv.logic_trace:
                try:
                    logic_trace_parsed = json.loads(cv.logic_trace)
                except Exception:
                    pass

            concls_out.append({
                "id": c.id,
                "claim": c.claim,
                "canonical_claim": c.canonical_claim,
                "conclusion_type": c.conclusion_type,
                "time_horizon_note": c.time_horizon_note,
                "author_confidence": c.author_confidence,
                "author_confidence_note": c.author_confidence_note,
                "status": str(c.status),
                "verdict": str(cv.verdict) if cv else None,
                "logic_trace": logic_trace_parsed,
                "role_fit": cv.role_fit if cv else None,
                "role_fit_note": cv.role_fit_note if cv else None,
                "logic_completeness": logic_info.get("completeness"),
                "logic_note": logic_info.get("logic_note"),
                "logic_summary": logic_info.get("summary"),
                "supporting_facts": supporting_facts,
                "supporting_conclusion_ids": logic_info.get("sup_concs", []),
                "solutions": conc_solutions.get(c.id, []),
                "conditional_assumption": c.conditional_assumption,
                "assumption_probability": c.assumption_probability,
                "conditional_monitoring_status": c.conditional_monitoring_status,
            })

        # ── Predictions (v2) ─────────────────────────────────────────────
        pred_verdict_map: dict[int, PredictionVerdict] = {}
        if pred_ids:
            for pv in (await session.exec(
                select(PredictionVerdict).where(PredictionVerdict.prediction_id.in_(pred_ids))
            )).all():
                pred_verdict_map[pv.prediction_id] = pv

        preds_out = []
        for p in all_preds:
            pv = pred_verdict_map.get(p.id)
            plogic = pred_logic_map.get(p.id, {})
            preds_out.append({
                "id": p.id,
                "claim": p.claim,
                "canonical_claim": p.canonical_claim,
                "verifiable_statement": p.verifiable_statement,
                "temporal_note": p.temporal_note,
                "author_confidence": p.author_confidence,
                "author_confidence_note": p.author_confidence_note,
                "status": str(p.status),
                "verdict": str(pv.verdict) if pv else None,
                "alignment_result": p.alignment_result,
                "alignment_evidence": p.alignment_evidence,
                "alignment_tier": p.alignment_tier,
                "conditional_assumption": p.conditional_assumption,
                "assumption_probability": p.assumption_probability,
                "conditional_monitoring_status": p.conditional_monitoring_status,
                "monitoring_source_org": p.monitoring_source_org,
                "monitoring_period_note": p.monitoring_period_note,
                "monitoring_start": str(p.monitoring_start) if p.monitoring_start else None,
                "monitoring_end": str(p.monitoring_end) if p.monitoring_end else None,
                "chain_summary": plogic.get("chain_summary"),
                "logic_validity": plogic.get("logic_validity"),
            })

        # ── Assumptions (v2) ─────────────────────────────────────────────
        assumps_out = []
        for a_obj in all_assumps:
            assumps_out.append({
                "id": a_obj.id,
                "condition_text": a_obj.condition_text,
                "canonical_condition": a_obj.canonical_condition,
                "verifiable_statement": a_obj.verifiable_statement,
                "temporal_type": a_obj.temporal_type,
                "temporal_note": a_obj.temporal_note,
                "is_verifiable": a_obj.is_verifiable,
                "alignment_result": a_obj.alignment_result,
                "alignment_evidence": a_obj.alignment_evidence,
            })

        # All ICs (flat list for the dedicated IC section)
        all_ics_flat = list((await session.exec(
            select(ImplicitCondition).where(ImplicitCondition.fact_id.in_(fact_ids))
        )).all()) if fact_ids else []
        ics_out = [
            {
                "id": ic.id,
                "fact_id": ic.fact_id,
                "conclusion_id": ic.conclusion_id,
                "condition_text": ic.condition_text,
                "verification_result": ic.verification_result,
                "verification_note": ic.verification_note,
                "vote_consensus": ic.vote_consensus,
                "vote_not_consensus": ic.vote_not_consensus,
                "consensus_trend": ic.consensus_trend,
                "consensus_trend_note": ic.consensus_trend_note,
            }
            for ic in all_ics_flat
        ]

        # Author stats
        stats_r = await session.exec(
            select(AuthorStats).where(AuthorStats.author_id == author_id)
        )
        stats = stats_r.first()

        # Post quality
        pqa_r = await session.exec(
            select(PostQualityAssessment).where(
                PostQualityAssessment.raw_post_id == raw_post_id
            )
        )
        pqa = pqa_r.first()

        def _noise_types(p):
            if not p or not p.noise_types:
                return []
            try:
                return json.loads(p.noise_types)
            except Exception:
                return []

        # Author group info
        author_group_data = None
        author_group_members = []
        if author.author_group_id:
            ag = await session.get(AuthorGroup, author.author_group_id)
            if ag:
                author_group_data = {
                    "id": ag.id,
                    "canonical_name": ag.canonical_name,
                    "canonical_role": ag.canonical_role,
                }
                other_r = await session.exec(
                    select(Author).where(
                        Author.author_group_id == author.author_group_id,
                        Author.id != author.id,
                    )
                )
                author_group_members = [
                    {"name": m.name, "platform": m.platform, "role": m.role}
                    for m in other_r.all()
                ]

        # Author stance profile
        stance_profile_data = None
        sp_r = await session.exec(
            select(AuthorStanceProfile).where(
                AuthorStanceProfile.author_id == author_id
            )
        )
        sp = sp_r.first()
        if sp:
            dist = {}
            if sp.stance_distribution:
                try:
                    dist = json.loads(sp.stance_distribution)
                except Exception:
                    pass
            stance_profile_data = {
                "dominant_stance": sp.dominant_stance,
                "dominant_stance_ratio": sp.dominant_stance_ratio,
                "total_analyzed": sp.total_analyzed,
                "distribution": dist,
            }

    return {
        "raw_post": {
            "id": rp.id,
            "url": rp.url,
            "author_name": rp.author_name,
            "source": rp.source,
            "posted_at": str(rp.posted_at) if rp.posted_at else None,
            "content_preview": (rp.content or "")[:400],
        },
        "facts": facts_out,
        "implicit_conditions": ics_out,
        "conclusions": concls_out,
        "predictions": preds_out,
        "assumptions": assumps_out,
        "author": {
            "id": author.id,
            "name": author.name,
            "platform": author.platform,
            "role": author.role,
            "expertise_areas": author.expertise_areas,
            "known_biases": author.known_biases,
            "credibility_tier": author.credibility_tier,
            "profile_note": author.profile_note,
            "author_group_id": author.author_group_id,
            "author_group": author_group_data,
            "author_group_members": author_group_members,
        },
        "stance_profile": stance_profile_data,
        "quality": {
            "uniqueness_score": pqa.uniqueness_score if pqa else None,
            "is_first_mover": pqa.is_first_mover if pqa else None,
            "similar_claim_count": pqa.similar_claim_count if pqa else None,
            "similar_author_count": pqa.similar_author_count if pqa else None,
            "uniqueness_note": pqa.uniqueness_note if pqa else None,
            "effectiveness_score": pqa.effectiveness_score if pqa else None,
            "noise_ratio": pqa.noise_ratio if pqa else None,
            "noise_types": _noise_types(pqa),
            "effectiveness_note": pqa.effectiveness_note if pqa else None,
            "stance_label": pqa.stance_label if pqa else None,
            "stance_note": pqa.stance_note if pqa else None,
        } if pqa else None,
        "stats": {
            "overall_credibility_score": stats.overall_credibility_score if stats else None,
            "total_posts_analyzed": stats.total_posts_analyzed if stats else None,
            "fact_accuracy_rate": stats.fact_accuracy_rate if stats else None,
            "conclusion_accuracy_rate": stats.conclusion_accuracy_rate if stats else None,
            "prediction_accuracy_rate": stats.prediction_accuracy_rate if stats else None,
            "logic_rigor_score": stats.logic_rigor_score if stats else None,
            "recommendation_reliability_rate": stats.recommendation_reliability_rate if stats else None,
            "content_uniqueness_score": stats.content_uniqueness_score if stats else None,
            "content_effectiveness_score": stats.content_effectiveness_score if stats else None,
        } if stats else None,
    }


# ── HTML 模板 ─────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Anchor — 观点验证系统</title>
  <style>
    :root {
      --bg: #0d0f1a;
      --surface: #161927;
      --surface2: #1e2235;
      --border: #2a2f4a;
      --text: #e2e8f0;
      --text-dim: #7c87a0;
      --accent: #6366f1;
      --accent2: #818cf8;
      --green: #22c55e;
      --red: #f87171;
      --yellow: #fbbf24;
      --blue: #60a5fa;
      --gray: #6b7280;
      --radius: 10px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; line-height: 1.65; min-height: 100vh; }

    /* ── Header ── */
    .header {
      position: sticky; top: 0; z-index: 100;
      background: rgba(13,15,26,0.96); backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--border);
      padding: 14px 28px;
      display: flex; align-items: center; gap: 20px;
    }
    .logo { font-size: 20px; font-weight: 800; color: var(--accent2); letter-spacing: -0.5px; flex-shrink: 0; }
    .url-form { flex: 1; display: flex; gap: 10px; max-width: 840px; }
    .url-input {
      flex: 1; padding: 10px 16px; border-radius: var(--radius);
      background: var(--surface2); border: 1px solid var(--border); color: var(--text);
      font-size: 14px; outline: none; transition: border-color 0.2s;
    }
    .url-input:focus { border-color: var(--accent); }
    .url-input::placeholder { color: var(--text-dim); }
    .analyze-btn {
      padding: 10px 28px; border-radius: var(--radius);
      background: var(--accent); border: none; color: #fff;
      font-size: 14px; font-weight: 600; cursor: pointer;
      transition: opacity 0.2s; white-space: nowrap;
    }
    .analyze-btn:hover { opacity: 0.85; }
    .analyze-btn:disabled { opacity: 0.4; cursor: not-allowed; }

    /* ── Layout ── */
    .main { max-width: 1440px; margin: 0 auto; padding: 28px; }

    /* ── Progress ── */
    .progress-section { margin-bottom: 28px; display: none; }
    .progress-steps { display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 20px; background: var(--surface); border-radius: var(--radius); border: 1px solid var(--border); }
    .step-pill { display: flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; font-size: 12px; background: var(--surface2); color: var(--text-dim); border: 1px solid var(--border); transition: all 0.25s; }
    .step-pill.active { border-color: var(--accent); color: var(--accent2); background: rgba(99,102,241,0.12); }
    .step-pill.done { border-color: var(--green); color: var(--green); background: rgba(34,197,94,0.08); }
    .step-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
    .step-pill.active .step-dot { animation: blink 1s ease-in-out infinite; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.25} }
    .status-bar { margin-top: 12px; padding: 10px 16px; border-radius: 8px; font-size: 13px; }
    .status-bar.info { background: rgba(99,102,241,0.1); border: 1px solid rgba(99,102,241,0.25); color: var(--accent2); }
    .status-bar.error { background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3); color: #fca5a5; white-space: pre-wrap; font-family: monospace; font-size: 12px; }

    /* ── Empty state ── */
    .empty-state { text-align: center; padding: 80px 24px; }
    .empty-icon { font-size: 56px; opacity: 0.4; margin-bottom: 20px; }
    .empty-title { font-size: 18px; color: var(--text-dim); }
    .empty-sub { font-size: 13px; color: var(--text-dim); margin-top: 8px; opacity: 0.7; }

    /* ── Section ── */
    .results-section { display: none; }
    .results-section.visible { display: block; }
    .section { margin-bottom: 36px; }
    .section-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
    .section-title { font-size: 15px; font-weight: 700; color: var(--accent2); }
    .section-count { padding: 2px 8px; border-radius: 999px; background: var(--surface2); color: var(--text-dim); font-size: 11px; font-weight: 600; }

    /* ── Cards grid ── */
    .cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; }

    /* ── Card ── */
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; transition: border-color 0.2s; }
    .card:hover { border-color: rgba(99,102,241,0.5); }
    .card-header { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 12px; }
    .card-id { color: var(--text-dim); font-size: 10px; font-weight: 700; flex-shrink: 0; padding-top: 4px; font-family: monospace; }
    .card-claim { font-size: 13px; line-height: 1.65; flex: 1; }
    .card-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }

    /* ── Badges ── */
    .badge { display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; line-height: 1.5; }
    .badge-true, .badge-confirmed, .badge-validated, .badge-consensus { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-false, .badge-refuted, .badge-invalidated, .badge-not_consensus { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    .badge-uncertain, .badge-partial { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-unavailable, .badge-pending, .badge-unverifiable, .badge-expired { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }
    .badge-predictive { background: rgba(96,165,250,0.12); color: var(--blue); border: 1px solid rgba(96,165,250,0.3); }
    .badge-retrospective { background: rgba(99,102,241,0.12); color: var(--accent2); border: 1px solid rgba(99,102,241,0.3); }
    .badge-tier1 { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-tier2 { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-tier3 { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }

    /* ── Collapsible detail ── */
    .toggle-btn { cursor: pointer; font-size: 11px; color: var(--text-dim); margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); user-select: none; display: flex; align-items: center; gap: 4px; }
    .toggle-btn::after { content: '▼'; font-size: 9px; }
    .toggle-btn.collapsed::after { content: '▶'; }
    .detail-block { padding-top: 10px; }
    .detail-text { font-size: 12px; color: var(--text-dim); line-height: 1.7; white-space: pre-wrap; word-break: break-word; margin-bottom: 6px; }
    .detail-label { font-size: 10px; font-weight: 700; color: var(--accent2); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; margin-top: 10px; }

    /* ── IC items ── */
    .ic-list { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
    .ic-item { padding: 10px 14px; background: var(--surface2); border-radius: 8px; border-left: 3px solid var(--border); }
    .ic-item.consensus { border-left-color: var(--green); }
    .ic-item.not_consensus { border-left-color: var(--red); }
    .ic-item.uncertain, .ic-item.pending { border-left-color: var(--yellow); }
    .ic-text { font-size: 12px; margin-bottom: 8px; }
    .ic-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .vote-text { font-size: 11px; color: var(--text-dim); }

    /* ── Conclusions table ── */
    .table-wrap { overflow-x: auto; border-radius: var(--radius); border: 1px solid var(--border); }
    .conc-table { width: 100%; border-collapse: collapse; }
    .conc-table th {
      background: var(--surface2); padding: 12px 18px; text-align: left;
      font-size: 11px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.07em;
      border-bottom: 1px solid var(--border); white-space: nowrap;
    }
    .conc-table td { padding: 18px; border-bottom: 1px solid var(--border); vertical-align: top; }
    .conc-table tr:last-child td { border-bottom: none; }
    .conc-table tbody tr:hover td { background: rgba(99,102,241,0.04); }
    .conc-claim-text { font-size: 13px; font-weight: 500; line-height: 1.6; margin-bottom: 10px; }
    .conc-meta-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .conc-quote { font-size: 11px; color: var(--text-dim); font-style: italic; margin-top: 6px; }

    /* ── Fact refs in table ── */
    .fact-ref { padding: 8px 12px; background: var(--surface2); border-radius: 8px; margin-bottom: 8px; border-left: 3px solid var(--border); }
    .fact-ref.role-supporting { border-left-color: var(--accent); }
    .fact-ref.role-assumption { border-left-color: var(--yellow); }
    .fact-ref-header { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
    .fact-ref-claim { font-size: 12px; color: var(--text-dim); line-height: 1.6; }

    /* ── Solution refs in table ── */
    .sol-ref { padding: 10px 14px; background: var(--surface2); border-radius: 8px; margin-bottom: 8px; }
    .sol-ref-header { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 6px; }
    .sol-action-tag { padding: 2px 8px; border-radius: 6px; background: rgba(99,102,241,0.2); color: var(--accent2); font-size: 11px; font-weight: 700; }
    .sol-claim { font-size: 12px; color: var(--text-dim); }
    .sol-baseline { font-size: 11px; color: var(--text-dim); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); }

    /* ── Verdict cell ── */
    .verdict-cell { text-align: center; min-width: 120px; }
    .verdict-main { display: flex; justify-content: center; margin-bottom: 10px; }
    .verdict-sub { font-size: 11px; color: var(--text-dim); text-align: left; }
    .verdict-sub-row { margin-bottom: 4px; }

    /* ── Bottom panels ── */
    .bottom-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; margin-bottom: 36px; }
    @media (max-width: 1100px) { .bottom-grid { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 700px) { .bottom-grid { grid-template-columns: 1fr; } .cards-grid { grid-template-columns: 1fr; } }
    .panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; }
    .panel-title { font-size: 14px; font-weight: 700; color: var(--accent2); margin-bottom: 20px; }

    /* ── Author panel ── */
    .info-table { width: 100%; border-collapse: collapse; }
    .info-table td { padding: 6px 0; vertical-align: top; font-size: 13px; }
    .info-table td:first-child { color: var(--text-dim); font-size: 12px; min-width: 80px; padding-right: 16px; padding-top: 8px; }
    .info-table tr + tr td { border-top: 1px solid var(--border); }
    .tier-badge { display: inline-flex; padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 700; }

    /* ── Quality panel ── */
    .quality-twin { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 20px; }
    .quality-card { text-align: center; padding: 16px 12px; background: var(--surface2); border-radius: 8px; }
    .quality-num { font-size: 36px; font-weight: 800; line-height: 1; }
    .quality-lbl { font-size: 11px; color: var(--text-dim); margin-top: 6px; }
    .quality-sub { font-size: 11px; margin-top: 4px; }

    /* ── Score bars ── */
    .score-divider { border: none; border-top: 1px solid var(--border); margin: 20px 0; }
    .score-overall-row { text-align: center; margin-bottom: 20px; }
    .score-overall-num { font-size: 48px; font-weight: 900; line-height: 1; }
    .score-overall-lbl { font-size: 12px; color: var(--text-dim); margin-top: 4px; }
    .score-rows { display: flex; flex-direction: column; gap: 10px; }
    .score-row { display: flex; align-items: center; gap: 12px; }
    .score-row-label { font-size: 12px; color: var(--text-dim); min-width: 90px; flex-shrink: 0; }
    .score-bar-track { flex: 1; height: 5px; background: var(--surface2); border-radius: 999px; overflow: hidden; }
    .score-bar-fill { height: 100%; border-radius: 999px; transition: width 1.2s cubic-bezier(0.25,1,0.5,1); }
    .fill-high { background: var(--green); }
    .fill-mid { background: var(--yellow); }
    .fill-low { background: var(--red); }
    .score-row-val { font-size: 12px; min-width: 40px; text-align: right; font-weight: 600; }
  </style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo">⚓ Anchor</div>
  <form class="url-form" id="form">
    <input class="url-input" id="urlInput" type="text" placeholder="输入 X (Twitter) 或微博内容链接..." required>
    <button class="analyze-btn" id="analyzeBtn" type="submit">分析</button>
  </form>
</div>

<!-- Main -->
<div class="main">

  <!-- Progress -->
  <div class="progress-section" id="progressSection">
    <div class="progress-steps" id="progressSteps"></div>
    <div id="statusBar"></div>
  </div>

  <!-- Empty state -->
  <div class="empty-state" id="emptyState">
    <div class="empty-icon">🔎</div>
    <div class="empty-title">输入链接开始分析</div>
    <div class="empty-sub">支持 X（Twitter）和微博的内容链接</div>
  </div>

  <!-- Results -->
  <div class="results-section" id="resultsSection">

    <!-- Facts -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">事实核查</div>
        <div class="section-count" id="factsCount"></div>
      </div>
      <div class="cards-grid" id="factsGrid"></div>
    </div>

    <!-- Implicit Conditions -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">隐含条件</div>
        <div class="section-count" id="icsCount"></div>
      </div>
      <div class="cards-grid" id="icsGrid"></div>
    </div>

    <!-- Conclusions Table -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">结论列表</div>
        <div class="section-count" id="conclusionsCount"></div>
      </div>
      <div class="table-wrap">
        <table class="conc-table">
          <thead>
            <tr>
              <th style="width:28%">结论</th>
              <th style="width:30%">事实 &amp; 隐含条件</th>
              <th style="width:22%">解决方案</th>
              <th style="width:20%">判定结果</th>
            </tr>
          </thead>
          <tbody id="conclusionsBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Author + Content Quality + Author Stats -->
    <div class="bottom-grid">
      <div class="panel">
        <div class="panel-title">作者档案</div>
        <div id="authorContent"></div>
      </div>
      <div class="panel">
        <div class="panel-title">本篇内容评估</div>
        <div id="contentQualityContent"></div>
      </div>
      <div class="panel">
        <div class="panel-title">作者历史综合评估</div>
        <div id="authorStatsContent"></div>
      </div>
    </div>

  </div>
</div>

<script>
const STEPS = [
  {n:1,  l:"采集"},
  {n:2,  l:"观点提取"},
  {n:3,  l:"作者档案"},
  {n:4,  l:"事实核查"},
  {n:5,  l:"隐含条件"},
  {n:6,  l:"逻辑评估"},
  {n:7,  l:"预测监控"},
  {n:8,  l:"方案模拟"},
  {n:9,  l:"逻辑映射"},
  {n:10, l:"裁定推导"},
  {n:11, l:"角色评估"},
  {n:12, l:"质量评估"},
  {n:13, l:"统计更新"},
  {n:14, l:"汇总结果"},
];

let evtSource = null;

document.getElementById('form').addEventListener('submit', e => {
  e.preventDefault();
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;
  startAnalysis(url);
});

function startAnalysis(url) {
  document.getElementById('emptyState').style.display = 'none';
  document.getElementById('resultsSection').classList.remove('visible');
  document.getElementById('progressSection').style.display = 'block';
  document.getElementById('analyzeBtn').disabled = true;

  const stepsEl = document.getElementById('progressSteps');
  stepsEl.innerHTML = STEPS.map(s =>
    `<div class="step-pill" id="sp${s.n}"><div class="step-dot"></div>${s.l}</div>`
  ).join('');
  setStatus('正在连接...', 'info');

  if (evtSource) evtSource.close();

  fetch('/analyze', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url})
  }).then(r => r.json()).then(({task_id}) => {
    evtSource = new EventSource('/stream/' + task_id);
    evtSource.onmessage = onEvent;
    evtSource.onerror = () => {
      setStatus('连接中断，请刷新重试', 'error');
      document.getElementById('analyzeBtn').disabled = false;
      evtSource.close();
    };
  }).catch(err => {
    setStatus('请求失败：' + err, 'error');
    document.getElementById('analyzeBtn').disabled = false;
  });
}

function onEvent(e) {
  const ev = JSON.parse(e.data);
  if (ev.type === 'step') {
    const el = document.getElementById('sp' + ev.num);
    if (el) { el.classList.add('active'); el.classList.remove('done'); }
    setStatus('⏳ ' + ev.label + '...', 'info');
  } else if (ev.type === 'step_done') {
    const el = document.getElementById('sp' + ev.num);
    if (el) { el.classList.remove('active'); el.classList.add('done'); }
    setStatus('✓ ' + ev.detail, 'info');
  } else if (ev.type === 'done') {
    evtSource.close();
    document.getElementById('analyzeBtn').disabled = false;
    STEPS.forEach(s => {
      const el = document.getElementById('sp' + s.n);
      if (el) { el.classList.remove('active'); el.classList.add('done'); }
    });
    setStatus('✅ 分析完成', 'info');
    renderAll(ev.data);
  } else if (ev.type === 'error') {
    evtSource.close();
    document.getElementById('analyzeBtn').disabled = false;
    setStatus('❌ 错误：' + ev.message, 'error');
  }
}

function setStatus(msg, cls) {
  document.getElementById('statusBar').innerHTML =
    `<div class="status-bar ${cls}">${msg}</div>`;
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

const RESULT_LABELS = {
  true: '✓ 已核实', false: '✗ 不实', uncertain: '? 不确定', unavailable: '— 无数据',
  pending: '… 待核查', confirmed: '✓ 已确认', refuted: '✗ 已反驳',
  partial: '△ 部分确认', unverifiable: '— 无法验证', validated: '✓ 有效',
  invalidated: '✗ 无效', expired: '⌛ 已过期',
};
const CONF_LABELS = { certain:'确定', likely:'可能', uncertain:'不确定', speculative:'猜测' };
const TIER_LABELS = { 1:'顶级权威', 2:'行业专家', 3:'知名评论员', 4:'普通KOL', 5:'未知' };

function badge(cls, text) {
  return `<span class="badge badge-${cls}">${text}</span>`;
}
function resultBadge(r) {
  if (!r) return badge('pending', '待处理');
  const key = r.replace(/[^a-z_]/g, '');
  return badge(key, RESULT_LABELS[r] || r);
}
function tierBadge(t) {
  if (!t) return '';
  return badge('tier' + t, 'Tier' + t);
}
function scoreColor(v) {
  if (v == null) return 'var(--text-dim)';
  if (v >= 0.7) return 'var(--green)';
  if (v >= 0.4) return 'var(--yellow)';
  return 'var(--red)';
}
function fillClass(v) {
  if (v == null) return 'fill-mid';
  if (v >= 0.7) return 'fill-high';
  if (v >= 0.4) return 'fill-mid';
  return 'fill-low';
}
function toggleDetail(id, btn) {
  const el = document.getElementById(id);
  if (!el) return;
  const hidden = el.style.display === 'none' || el.style.display === '';
  el.style.display = hidden ? 'block' : 'none';
  btn.classList.toggle('collapsed', !hidden);
}

function icItemHtml(ic) {
  const cls = ic.verification_result || 'pending';
  const voteStr = ic.vote_consensus != null
    ? `<span class="vote-text">共识 ${ic.vote_consensus} / 非共识 ${ic.vote_not_consensus}</span>`
    : '';
  const trend = ic.consensus_trend
    ? `<span style="font-size:11px;color:var(--text-dim)">${trendIcon(ic.consensus_trend)} ${ic.consensus_trend}</span>`
    : '';
  const noteHtml = ic.verification_note
    ? `<div class="detail-text" style="margin-top:8px;">${esc(ic.verification_note)}</div>`
    : '';
  return `
<div class="ic-item ${cls}">
  <div class="ic-text">${esc(ic.condition_text)}</div>
  <div class="ic-meta">
    ${resultBadge(ic.verification_result)}
    ${voteStr}
    ${trend}
  </div>
  ${noteHtml}
</div>`;
}

function trendIcon(t) {
  return t === 'strengthening' ? '↑' : t === 'weakening' ? '↓' : t === 'stable' ? '→' : '';
}

// ─── Render all ───────────────────────────────────────────────────────────────

function renderAll(data) {
  document.getElementById('resultsSection').classList.add('visible');
  renderFacts(data.facts || []);
  renderICs(data.implicit_conditions || []);
  renderConclusions(data.conclusions || [], data.facts || []);

  // 收集所有 role_fit 评估（结论 + 解决方案）供作者面板独立展示
  const roleFitItems = [];
  (data.conclusions || []).forEach(c => {
    if (c.role_fit) roleFitItems.push({
      label: c.canonical_claim || c.claim || '',
      role_fit: c.role_fit,
      note: c.role_fit_note || null,
      kind: 'conclusion',
    });
    (c.solutions || []).forEach(s => {
      if (s.role_fit) roleFitItems.push({
        label: s.claim || '',
        role_fit: s.role_fit,
        note: s.role_fit_note || null,
        kind: 'solution',
      });
    });
  });

  renderAuthor(data.author, roleFitItems);
  renderContentQuality(data.quality, data.stance_profile);
  renderAuthorStats(data.stats);
}

// ─── Facts ────────────────────────────────────────────────────────────────────

function renderFacts(facts) {
  document.getElementById('factsCount').textContent = facts.length + ' 条';
  const grid = document.getElementById('factsGrid');
  if (!facts.length) {
    grid.innerHTML = '<div style="color:var(--text-dim);padding:8px;">无事实</div>';
    return;
  }
  grid.innerHTML = facts.map(f => {
    const detailId = 'fd' + f.id;
    const hasDetail = f.evidence_summary || f.evaluator_notes || f.verification_method || (f.refs && f.refs.length);
    const icHtml = f.implicit_conditions && f.implicit_conditions.length
      ? `<div style="margin-top:14px;"><div style="font-size:10px;font-weight:700;color:var(--accent2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;">隐含条件</div><div class="ic-list">${f.implicit_conditions.map(icItemHtml).join('')}</div></div>`
      : '';
    const notVerifiableNote = !f.is_verifiable
      ? `<div class="detail-text" style="margin-top:8px;font-style:italic;">此事实标记为不可核实</div>`
      : '';
    return `
<div class="card">
  <div class="card-header">
    <div class="card-id">F${f.id}</div>
    <div class="card-claim">${esc(f.claim)}</div>
  </div>
  <div class="card-meta">
    ${resultBadge(f.result)}
    ${tierBadge(f.evidence_tier)}
    ${!f.is_verifiable ? badge('unavailable','不可核实') : ''}
  </div>
  ${notVerifiableNote}
  ${hasDetail ? `
  <div class="toggle-btn collapsed" onclick="toggleDetail('${detailId}',this)">核查详情</div>
  <div id="${detailId}" style="display:none;" class="detail-block">
    ${f.evidence_summary ? `<div class="detail-label">核查摘要</div><div class="detail-text">${esc(f.evidence_summary)}</div>` : ''}
    ${f.evaluator_notes ? `<div class="detail-label">评估备注</div><div class="detail-text">${esc(f.evaluator_notes)}</div>` : ''}
    ${f.verification_method ? `<div class="detail-label">验证方法</div><div class="detail-text">${esc(f.verification_method)}</div>` : ''}
    ${(f.refs && f.refs.length) ? `<div class="detail-label">参考来源</div>${f.refs.map(r => `<div class="detail-text">📌 [${esc(r.org)}] ${esc(r.desc)}${r.url ? ' — <a href="'+esc(r.url)+'" target="_blank" style="font-size:11px;">链接</a>' : ''}</div>`).join('')}` : ''}
  </div>` : ''}
  ${icHtml}
</div>`;
  }).join('');
}

// ─── Implicit Conditions ──────────────────────────────────────────────────────

function renderICs(ics) {
  document.getElementById('icsCount').textContent = ics.length + ' 条';
  const grid = document.getElementById('icsGrid');
  if (!ics.length) {
    grid.innerHTML = '<div style="color:var(--text-dim);padding:8px;">无隐含条件</div>';
    return;
  }
  grid.innerHTML = ics.map(ic => {
    const parent = ic.fact_id ? 'Fact #' + ic.fact_id : 'Conclusion #' + ic.conclusion_id;
    const trendNote = ic.consensus_trend_note
      ? `<div class="detail-text" style="margin-top:8px;">📊 ${esc(ic.consensus_trend_note)}</div>`
      : '';
    return `
<div class="card">
  <div class="card-header">
    <div class="card-id" style="font-size:9px;">${parent}</div>
    <div class="card-claim">${esc(ic.condition_text)}</div>
  </div>
  <div class="card-meta">
    ${resultBadge(ic.verification_result)}
    ${ic.vote_consensus != null ? `<span class="vote-text" style="font-size:11px;color:var(--text-dim);">共识 ${ic.vote_consensus} / 非共识 ${ic.vote_not_consensus}</span>` : ''}
    ${ic.consensus_trend ? `<span style="font-size:11px;color:var(--text-dim);">${trendIcon(ic.consensus_trend)} ${ic.consensus_trend}</span>` : ''}
  </div>
  ${ic.verification_note ? `<div class="detail-text" style="margin-top:10px;">${esc(ic.verification_note)}</div>` : ''}
  ${trendNote}
</div>`;
  }).join('');
}

// ─── Conclusions Table ────────────────────────────────────────────────────────

function renderConclusions(conclusions, allFacts) {
  document.getElementById('conclusionsCount').textContent = conclusions.length + ' 个';
  const body = document.getElementById('conclusionsBody');
  if (!conclusions.length) {
    body.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-dim);padding:40px;">无结论</td></tr>';
    return;
  }

  body.innerHTML = conclusions.map((c, ci) => {
    // ── Col 1: Conclusion ──
    const typeLabel = c.conclusion_type === 'predictive' ? '预测型' : '回顾型';
    const confLabel = c.author_confidence ? (CONF_LABELS[c.author_confidence] || c.author_confidence) : null;
    const col1 = `
<div class="conc-claim-text">${esc(c.claim)}</div>
<div class="conc-meta-row">
  ${badge(c.conclusion_type, typeLabel)}
  ${confLabel ? badge(c.author_confidence, confLabel) : ''}
</div>
${c.author_confidence_note ? `<div class="conc-quote">"${esc(c.author_confidence_note)}"</div>` : ''}
${c.time_horizon_note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">⏱ ${esc(c.time_horizon_note)}</div>` : ''}
${c.logic_summary ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">💡 ${esc(c.logic_summary)}</div>` : ''}`;

    // ── Col 2: Supporting Facts + ICs ──
    const sfItems = (c.supporting_facts || []).map(f => {
      const roleCls = f.role === 'assumption' ? 'role-assumption' : 'role-supporting';
      const roleLabel = f.role === 'assumption' ? '假设' : '支撑';
      const icsInFact = (f.implicit_conditions && f.implicit_conditions.length)
        ? `<div class="ic-list" style="margin-top:8px;">${f.implicit_conditions.map(ic => icItemHtml(ic)).join('')}</div>`
        : '';
      return `
<div class="fact-ref ${roleCls}">
  <div class="fact-ref-header">
    <span style="font-size:10px;color:var(--text-dim);font-family:monospace;">F${f.id}</span>
    ${badge(f.role, roleLabel)}
    ${resultBadge(f.result)}
  </div>
  <div class="fact-ref-claim">${esc(f.claim)}</div>
  ${icsInFact}
</div>`;
    });
    const col2 = sfItems.length ? sfItems.join('') : `<span style="color:var(--text-dim);font-size:12px;">无关联事实</span>`;

    // ── Col 3: Solutions ──
    const solItems = (c.solutions || []).map(s => {
      return `
<div class="sol-ref">
  <div class="sol-ref-header">
    ${s.action_type ? `<span class="sol-action-tag">${s.action_type.toUpperCase()}</span>` : ''}
    <span style="font-size:13px;font-weight:600;">${esc(s.action_target || '')}</span>
    ${resultBadge(s.verdict)}
  </div>
  <div class="sol-claim">${esc(s.claim)}</div>
  ${s.baseline_value ? `<div class="sol-baseline">基准：${esc(s.baseline_metric || '')} = <strong>${esc(s.baseline_value)}</strong></div>` : ''}
  ${s.monitoring_period_note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:4px;">📅 ${esc(s.monitoring_period_note)}</div>` : ''}
</div>`;
    });
    const col3 = solItems.length ? solItems.join('') : `<span style="color:var(--text-dim);font-size:12px;">无解决方案</span>`;

    // ── Col 4: Verdict ──
    const col4 = (() => {
      if (!c.verdict || c.verdict === 'null') {
        return `<div class="verdict-cell"><span style="color:var(--text-dim);font-size:12px;">待裁定</span></div>`;
      }
      const lcStr = c.logic_completeness ? `<div class="verdict-sub-row">逻辑：${esc(c.logic_completeness)}</div>` : '';
      return `
<div class="verdict-cell">
  <div class="verdict-main">${resultBadge(c.verdict)}</div>
  <div class="verdict-sub">${lcStr}</div>
</div>`;
    })();

    return `<tr><td>${col1}</td><td>${col2}</td><td>${col3}</td><td>${col4}</td></tr>`;
  }).join('');
}

// ─── Author ───────────────────────────────────────────────────────────────────

function renderAuthor(a, roleFitItems) {
  if (!a) { document.getElementById('authorContent').innerHTML = '<div style="color:var(--text-dim)">暂无数据</div>'; return; }
  const tier = a.credibility_tier;
  const tierColor = tier === 1 ? 'var(--green)' : tier === 2 ? 'var(--yellow)' : 'var(--text-dim)';
  const tierLbl = tier ? `Tier${tier} · ${TIER_LABELS[tier] || '?'}` : '—';

  // 跨平台关联
  let groupHtml = '';
  if (a.author_group && (a.author_group_members && a.author_group_members.length > 0)) {
    const members = a.author_group_members.map(m =>
      `<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;background:rgba(99,102,241,0.1);border-radius:6px;font-size:11px;margin-right:4px;">${esc(m.platform)} · ${esc(m.name)}</span>`
    ).join('');
    groupHtml = `<tr><td>跨平台关联</td><td style="font-size:12px;">${members}</td></tr>`;
  }

  const reprofileBtn = a.id
    ? `<button onclick="reprofileAuthor(${a.id})" style="margin-top:14px;padding:6px 14px;border-radius:6px;background:rgba(99,102,241,0.15);border:1px solid rgba(99,102,241,0.4);color:var(--accent2);font-size:12px;cursor:pointer;" title="强制重新联网查询作者档案（修正 tier=5 或过时数据）">🔄 重新联网查询档案</button>`
    : '';

  // ── 专业背景评估模块 ──────────────────────────────────────────────────────
  let bgAssessHtml = '';
  const items = roleFitItems || [];

  // 可信度不足时的警告（tier 4/5 或未知）
  let credWarning = '';
  if (!tier || tier >= 4) {
    const warnMsg = tier === 4
      ? '该作者为普通媒体/KOL，专业背景有限，观点仅供参考。'
      : '该作者专业背景不明，无法评估其观点的权威性。';
    credWarning = `
<div style="margin-bottom:10px;padding:8px 12px;border-radius:6px;background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);font-size:12px;color:#fbbf24;">
  ⚠ ${warnMsg}
</div>`;
  }

  // role_fit 汇总
  const RF_COLOR = { appropriate: 'var(--green)', questionable: '#fbbf24', mismatched: 'var(--red)' };
  const RF_LABEL = { appropriate: '匹配', questionable: '存疑', mismatched: '不匹配' };
  const hasIssues = items.some(r => r.role_fit === 'questionable' || r.role_fit === 'mismatched');

  let rfSummaryHtml = '';
  if (items.length > 0) {
    const issueCnt = items.filter(r => r.role_fit !== 'appropriate').length;
    const summaryLine = issueCnt > 0
      ? `<div style="font-size:12px;color:#fbbf24;margin-bottom:8px;">⚠ ${issueCnt} 个观点存在角色匹配问题（不影响裁定，仅供参考）</div>`
      : `<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">所有观点与作者背景匹配（不影响裁定）</div>`;

    const rfRows = items.map(r => {
      const col = RF_COLOR[r.role_fit] || 'var(--text-dim)';
      const lbl = RF_LABEL[r.role_fit] || r.role_fit;
      const noteHtml = r.note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:2px;font-style:italic;">${esc(r.note)}</div>` : '';
      const kindBadge = r.kind === 'solution'
        ? `<span style="font-size:10px;background:rgba(99,102,241,0.15);padding:1px 5px;border-radius:4px;margin-right:4px;">建议</span>`
        : '';
      return `
<div style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.05);">
  <div style="display:flex;align-items:flex-start;gap:8px;">
    <span style="color:${col};font-weight:600;font-size:11px;white-space:nowrap;padding-top:1px;">${lbl}</span>
    <span style="font-size:12px;color:var(--text-secondary);flex:1;">${kindBadge}${esc(r.label)}</span>
  </div>
  ${noteHtml}
</div>`;
    }).join('');

    rfSummaryHtml = summaryLine + `<div style="border-top:1px solid rgba(255,255,255,0.07);">${rfRows}</div>`;
  }

  if (credWarning || rfSummaryHtml) {
    bgAssessHtml = `
<div style="margin-top:16px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.1);">
  <div style="font-size:11px;font-weight:600;letter-spacing:.06em;color:var(--text-dim);text-transform:uppercase;margin-bottom:10px;">专业背景评估</div>
  ${credWarning}${rfSummaryHtml}
</div>`;
  }

  document.getElementById('authorContent').innerHTML = `
<table class="info-table">
  <tr><td>姓名</td><td><strong style="font-size:15px;">${esc(a.name || '—')}</strong></td></tr>
  <tr><td>平台</td><td>${esc(a.platform || '—')}</td></tr>
  <tr><td>角色</td><td>${esc(a.role || '—')}</td></tr>
  <tr><td>可信度</td><td><span class="tier-badge" style="color:${tierColor};background:transparent;padding-left:0;">${tierLbl}</span></td></tr>
  <tr><td>专业领域</td><td style="color:var(--text-dim)">${esc(a.expertise_areas || '—')}</td></tr>
  <tr><td>已知偏见</td><td style="color:var(--text-dim)">${esc(a.known_biases || '—')}</td></tr>
  ${a.profile_note ? `<tr><td>简介</td><td style="color:var(--text-dim);font-size:12px;">${esc(a.profile_note)}</td></tr>` : ''}
  ${groupHtml}
</table>
${bgAssessHtml}
${reprofileBtn}`;
}

async function reprofileAuthor(authorId) {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '⏳ 查询中...';
  try {
    const resp = await fetch('/reprofile', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({author_id: authorId}),
    });
    const data = await resp.json();
    if (data.ok) {
      btn.textContent = `✓ 已更新：Tier${data.credibility_tier} ${esc(data.role || '')}`;
      btn.style.color = 'var(--green)';
      btn.style.borderColor = 'rgba(34,197,94,0.4)';
    } else {
      btn.textContent = '✗ 查询失败';
      btn.style.color = 'var(--red)';
    }
  } catch(e) {
    btn.textContent = '✗ 请求失败: ' + e;
    btn.style.color = 'var(--red)';
  }
}

// ─── Content Quality (本篇内容评估) ──────────────────────────────────────────

const STANCE_COLORS = {
  '看涨/多头':  '#22c55e',
  '看跌/空头':  '#f87171',
  '中立/客观':  '#7c87a0',
  '警告/防御':  '#fbbf24',
  '批判/质疑':  '#fb923c',
  '政策倡导':   '#a78bfa',
  '教育/分析':  '#60a5fa',
  '其他':       '#6b7280',
};

function renderContentQuality(q, stanceProfile) {
  let html = '';
  if (!q) {
    html = '<div style="color:var(--text-dim);font-size:13px;">质量评估暂无数据</div>';
    document.getElementById('contentQualityContent').innerHTML = html;
    return;
  }

  const uPct = q.uniqueness_score != null ? (q.uniqueness_score * 100).toFixed(0) : null;
  const ePct = q.effectiveness_score != null ? (q.effectiveness_score * 100).toFixed(0) : null;

  html += `
<div class="quality-twin">
  <div class="quality-card">
    <div class="quality-num" style="color:${scoreColor(q.uniqueness_score)}">${uPct != null ? uPct + '%' : '—'}</div>
    <div class="quality-lbl">内容独特性</div>
    <div class="quality-sub" style="color:var(--text-dim)">${q.is_first_mover ? '🥇 首发内容' : '已有类似观点'}</div>
  </div>
  <div class="quality-card">
    <div class="quality-num" style="color:${scoreColor(q.effectiveness_score)}">${ePct != null ? ePct + '%' : '—'}</div>
    <div class="quality-lbl">内容有效性</div>
    <div class="quality-sub" style="color:var(--text-dim)">噪声率 ${q.noise_ratio != null ? (q.noise_ratio*100).toFixed(0)+'%' : '—'}</div>
  </div>
</div>`;

  if (q.uniqueness_note) html += `<div class="detail-text" style="margin-bottom:8px;">${esc(q.uniqueness_note)}</div>`;
  if (q.effectiveness_note) html += `<div class="detail-text" style="margin-bottom:8px;">${esc(q.effectiveness_note)}</div>`;
  if (q.noise_types && q.noise_types.length) {
    html += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:12px;">噪声类型：${q.noise_types.map(t => `<span style="padding:1px 6px;background:rgba(248,113,113,0.1);border-radius:4px;color:var(--red);margin-right:4px;">${esc(t)}</span>`).join('')}</div>`;
  }

  // ── 当篇立场分析 ──
  if (q.stance_label) {
    const stanceColor = STANCE_COLORS[q.stance_label] || '#7c87a0';
    html += `<hr class="score-divider">
<div style="margin-bottom:10px;">
  <div class="detail-label" style="margin-bottom:8px;">当篇立场</div>
  <span style="display:inline-block;padding:4px 14px;border-radius:999px;font-size:13px;font-weight:700;color:${stanceColor};background:${stanceColor}18;border:1px solid ${stanceColor}44;">${esc(q.stance_label)}</span>
</div>`;
    if (q.stance_note) {
      html += `<div class="detail-text">${esc(q.stance_note)}</div>`;
    }
  }

  // ── 作者立场历史分布 ──
  if (stanceProfile && stanceProfile.total_analyzed > 0) {
    const domColor = STANCE_COLORS[stanceProfile.dominant_stance] || '#7c87a0';
    const domPct = stanceProfile.dominant_stance_ratio != null
      ? (stanceProfile.dominant_stance_ratio * 100).toFixed(0) + '%' : '—';
    html += `<hr class="score-divider">
<div style="margin-bottom:10px;">
  <div class="detail-label" style="margin-bottom:8px;">作者立场历史（${stanceProfile.total_analyzed} 篇）</div>
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
    <span style="display:inline-block;padding:3px 12px;border-radius:999px;font-size:12px;font-weight:700;color:${domColor};background:${domColor}18;border:1px solid ${domColor}44;">${esc(stanceProfile.dominant_stance || '—')}</span>
    <span style="font-size:12px;color:var(--text-dim);">主导立场 · ${domPct}</span>
  </div>`;

    // 分布条
    const dist = stanceProfile.distribution || {};
    const total = stanceProfile.total_analyzed;
    const entries = Object.entries(dist).sort((a,b) => b[1]-a[1]);
    if (entries.length > 0) {
      html += `<div style="display:flex;flex-direction:column;gap:5px;">`;
      for (const [stance, count] of entries) {
        const pct = (count / total * 100).toFixed(0);
        const color = STANCE_COLORS[stance] || '#6b7280';
        html += `<div style="display:flex;align-items:center;gap:8px;">
  <div style="font-size:11px;color:var(--text-dim);min-width:80px;flex-shrink:0;">${esc(stance)}</div>
  <div style="flex:1;height:4px;background:var(--surface2);border-radius:999px;overflow:hidden;">
    <div style="height:100%;width:${pct}%;background:${color};border-radius:999px;"></div>
  </div>
  <div style="font-size:11px;color:var(--text-dim);min-width:28px;text-align:right;">${count}</div>
</div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;
  }

  document.getElementById('contentQualityContent').innerHTML = html;
}

// ─── Author Stats (作者历史综合评估) ─────────────────────────────────────────

function renderAuthorStats(stats) {
  let html = '';
  if (!stats || stats.overall_credibility_score == null) {
    html = '<div style="color:var(--text-dim);font-size:13px;">作者综合评估暂无数据</div>';
    document.getElementById('authorStatsContent').innerHTML = html;
    return;
  }

  const ov = stats.overall_credibility_score;
  const ovColor = scoreColor(ov / 100);
  const SCORE_ROWS = [
    ['① 事实准确率', stats.fact_accuracy_rate],
    ['② 结论准确性', stats.conclusion_accuracy_rate],
    ['③ 预测准确性', stats.prediction_accuracy_rate],
    ['④ 逻辑严谨性', stats.logic_rigor_score],
    ['⑤ 建议可靠性', stats.recommendation_reliability_rate],
    ['⑥ 内容独特性', stats.content_uniqueness_score],
    ['⑦ 内容有效性', stats.content_effectiveness_score],
  ];
  html += `
<div class="score-overall-row">
  <div class="score-overall-num" style="color:${ovColor}">${ov.toFixed(1)}</div>
  <div class="score-overall-lbl">综合可信度评分（满分 100）</div>
</div>
<div class="score-rows">
${SCORE_ROWS.map(([lbl, v]) => {
  if (v == null) return `<div class="score-row"><div class="score-row-label">${lbl}</div><div class="score-bar-track"><div class="score-bar-fill fill-mid" style="width:0%"></div></div><div class="score-row-val" style="color:var(--text-dim)">N/A</div></div>`;
  const pct = (v * 100).toFixed(0);
  return `<div class="score-row"><div class="score-row-label">${lbl}</div><div class="score-bar-track"><div class="score-bar-fill ${fillClass(v)}" style="width:${pct}%"></div></div><div class="score-row-val" style="color:${scoreColor(v)}">${pct}%</div></div>`;
}).join('')}
</div>`;

  if (stats.total_posts_analyzed != null) {
    html += `<div style="font-size:11px;color:var(--text-dim);margin-top:14px;">基于 ${stats.total_posts_analyzed} 篇内容分析</div>`;
  }

  document.getElementById('authorStatsContent').innerHTML = html;
}
</script>
</body>
</html>"""


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
