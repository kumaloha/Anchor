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
from fastapi.staticfiles import StaticFiles
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
            # v3 新字段
            ("facts",       "alignment_vagueness",    "TEXT"),
            ("conclusions", "is_core_conclusion",     "INTEGER DEFAULT 0"),
            ("conclusions", "is_in_cycle",            "INTEGER DEFAULT 0"),
            ("logics",      "condition_ids",          "TEXT"),
            # v2.2 语义化判定标签
            ("facts",        "fact_verdict",        "TEXT"),
            ("facts",        "fact_source_tier",    "TEXT"),
            ("conclusions",  "conclusion_verdict",  "TEXT"),
            ("conclusions",  "prediction_verdict",  "TEXT"),
            ("conditions",   "condition_verdict",   "TEXT"),
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
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")

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
        from anchor.tracker.post_quality_evaluator import PostQualityEvaluator
        from anchor.tracker.role_evaluator import RoleEvaluator

        from anchor.tracker.verdict_deriver import VerdictDeriver
        from anchor.models import (
            Author, AuthorGroup, AuthorStats, AuthorStanceProfile,
            Condition, Conclusion, ConclusionVerdict, Fact, FactEvaluation,
            Logic, MonitoredSource, PostQualityAssessment,
            RawPost, Solution, SolutionAssessment,
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
            conditions = list((await session.exec(
                select(Condition).where(Condition.raw_post_id == raw_post_id)
            )).all())
            concls = list((await session.exec(
                select(Conclusion).where(Conclusion.source_url == post_url)
            )).all())
            sols = list((await session.exec(
                select(Solution).where(Solution.source_url == post_url)
            )).all())
            conc_ids = [c.id for c in concls]
            sol_ids = [s.id for s in sols]
            fact_ids = [f.id for f in facts]
            cond_ids = [c.id for c in conditions]
            all_logics = list((await session.exec(select(Logic))).all())
            logics = [
                l for l in all_logics
                if (l.logic_type == "inference" and l.conclusion_id in conc_ids)
                or (l.logic_type == "derivation" and l.solution_id in sol_ids)
            ]

        await _emit(q, {
            "type": "step_done", "num": 2,
            "detail": (
                f"提取：{len(facts)} 事实，{len(conditions)} 条件，"
                f"{len(concls)} 结论，{len(sols)} 解决方案"
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
            for cond in conditions:
                cond_obj = await session.get(Condition, cond.id)
                if cond_obj:
                    await aligner.align_condition(cond_obj, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 5, "detail": "现实对齐完成"})

        # ── Step 3：预测型结论监控配置 ──────────────────────────────────
        from anchor.tracker.conclusion_monitor import ConclusionMonitor
        predictive_concls = [c for c in concls if c.conclusion_type == "predictive"]
        await _emit(q, {"type": "step", "num": 6, "label": f"Step 3 — 预测型结论监控配置（{len(predictive_concls)} 条）"})

        conc_monitor = ConclusionMonitor()
        if predictive_concls:
            async with AsyncSessionLocal() as session:
                for pc in predictive_concls:
                    c = await session.get(Conclusion, pc.id)
                    if c:
                        await conc_monitor.setup(c, session)
                await session.commit()

        await _emit(q, {
            "type": "step_done", "num": 6,
            "detail": f"配置 {len(predictive_concls)} 个预测型结论监控",
        })

        # ── Step 4：裁定推导（VerdictDeriver）────────────────────────────
        await _emit(q, {"type": "step", "num": 7, "label": "Step 4 — 裁定推导"})

        deriver = VerdictDeriver()
        async with AsyncSessionLocal() as session:
            for conc in concls:
                c = await session.get(Conclusion, conc.id)
                if c:
                    await deriver.derive_conclusion(c, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 7, "detail": "裁定完成"})

        # ── Step 5：角色匹配评估 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 8, "label": "Step 5 — 角色匹配评估"})

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

        await _emit(q, {"type": "step_done", "num": 8, "detail": "角色匹配评估完成"})

        # ── Step 6：内容质量评估 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 9, "label": "Step 6 — 内容质量评估"})

        post_quality_evaluator = PostQualityEvaluator()
        async with AsyncSessionLocal() as session:
            rp = await session.get(RawPost, raw_post_id)
            a = await session.get(Author, author_id)
            await post_quality_evaluator.assess(rp, a, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 9, "detail": "内容质量评估完成"})

        # ── Step 7：作者统计更新 ──────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 10, "label": "Step 7 — 作者综合统计更新"})

        author_stats_updater = AuthorStatsUpdater()
        async with AsyncSessionLocal() as session:
            a = await session.get(Author, author_id)
            await author_stats_updater.update(a, session)
            await session.commit()

        await _emit(q, {"type": "step_done", "num": 10, "detail": "统计更新完成"})

        # ── 汇总结果 ──────────────────────────────────────────────────────
        await _emit(q, {"type": "step", "num": 11, "label": "汇总结果"})
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
        Author, AuthorGroup, AuthorStats, AuthorStanceProfile,
        Condition, Conclusion, ConclusionVerdict, Fact, FactEvaluation,
        Logic, PostQualityAssessment,
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

        facts_out = []
        for f in all_facts:
            fe = fact_evals.get(f.id)
            refs = list((await session.exec(
                select(VerificationReference).where(VerificationReference.fact_id == f.id)
            )).all())
            facts_out.append({
                "id": f.id,
                "claim": f.claim,
                "canonical_claim": f.canonical_claim,
                "verifiable_statement": f.verifiable_statement,
                "verifiable_expression": f.verifiable_expression,
                "is_verifiable": f.is_verifiable,
                "fact_verdict": f.fact_verdict,
                "fact_source_tier": f.fact_source_tier,
                "alignment_evidence": f.alignment_evidence,
                "alignment_tier": f.alignment_tier,
                "temporal_type": f.temporal_type,
                "temporal_note": f.temporal_note,
                "refs": [
                    {"org": r.organization, "desc": r.data_description, "url": r.url}
                    for r in refs
                ],
            })

        fact_map = {f["id"]: f for f in facts_out}

        # ── Conditions ────────────────────────────────────────────────────
        all_conds = list((await session.exec(
            select(Condition).where(Condition.raw_post_id == raw_post_id)
        )).all())
        cond_ids = [c.id for c in all_conds]

        conditions_out = []
        for cond in all_conds:
            conditions_out.append({
                "id": cond.id,
                "condition_type": cond.condition_type,
                "condition_text": cond.condition_text,
                "verifiable_statement": cond.verifiable_statement,
                "temporal_note": cond.temporal_note,
                "is_consensus": cond.is_consensus,
                "is_verifiable": cond.is_verifiable,
                "condition_verdict": cond.condition_verdict,
                "alignment_evidence": cond.alignment_evidence,
            })

        # ── Conclusions & Solutions ───────────────────────────────────────
        all_concls = list((await session.exec(
            select(Conclusion).where(Conclusion.source_url == post_url)
        )).all())
        conc_ids = [c.id for c in all_concls]

        all_sols = list((await session.exec(
            select(Solution).where(Solution.source_url == post_url)
        )).all())
        sol_ids = [s.id for s in all_sols]

        # Logics for this analysis
        all_logics = list((await session.exec(select(Logic))).all())
        our_logics = [
            l for l in all_logics
            if (l.logic_type == "inference" and l.conclusion_id in conc_ids)
            or (l.logic_type == "derivation" and l.solution_id in sol_ids)
        ]

        # conclusion_id → logic info
        conc_logic_map: dict[int, dict] = {}
        # solution_id → source_conclusion_ids
        sol_source_map: dict[int, list[int]] = {}
        for l in our_logics:
            if l.logic_type == "inference" and l.conclusion_id:
                conc_logic_map[l.conclusion_id] = {
                    "supporting": json.loads(l.supporting_fact_ids or "[]"),
                    "conditions": json.loads(l.condition_ids or "[]"),
                    "sup_concs": json.loads(l.supporting_conclusion_ids or "[]"),
                    "chain_summary": l.chain_summary,
                    "logic_validity": l.logic_validity,
                    "logic_note": l.logic_note,
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
                "verifiable_statement": c.verifiable_statement,
                "conclusion_type": c.conclusion_type,
                "temporal_note": c.temporal_note,
                "time_horizon_note": c.time_horizon_note,
                "is_core_conclusion": c.is_core_conclusion,
                "is_in_cycle": c.is_in_cycle,
                "author_confidence": c.author_confidence,
                "author_confidence_note": c.author_confidence_note,
                "status": str(c.status),
                "verdict": str(cv.verdict) if cv else None,
                "conclusion_verdict": c.conclusion_verdict,
                "prediction_verdict": c.prediction_verdict,
                "logic_trace": logic_trace_parsed,
                "role_fit": cv.role_fit if cv else None,
                "role_fit_note": cv.role_fit_note if cv else None,
                "chain_summary": logic_info.get("chain_summary"),
                "logic_validity": logic_info.get("logic_validity"),
                "logic_note": logic_info.get("logic_note"),
                "alignment_evidence": c.alignment_evidence,
                "supporting_facts": supporting_facts,
                "supporting_conclusion_ids": logic_info.get("sup_concs", []),
                "supporting_condition_ids": logic_info.get("conditions", []),
                "solutions": conc_solutions.get(c.id, []),
            })

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
        "conditions": conditions_out,
        "conclusions": concls_out,
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
    /* 通用裁定 */
    .badge-confirmed, .badge-validated { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-refuted, .badge-invalidated { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    .badge-partial { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-unavailable, .badge-pending, .badge-unverifiable, .badge-expired { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }
    /* v2.2 事实判定 */
    .badge-credible { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-vague { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-unreliable { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    /* v2.2 预测型结论判定 */
    .badge-accurate { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-directional { background: rgba(96,165,250,0.12); color: var(--blue); border: 1px solid rgba(96,165,250,0.3); }
    .badge-off_target { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-wrong { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    /* v2.2 条件判定 */
    .badge-consensus { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-non_consensus { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-disputed { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    .badge-strong_assumption { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    .badge-likely_assumption { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    /* 结论类型 */
    .badge-predictive { background: rgba(96,165,250,0.12); color: var(--blue); border: 1px solid rgba(96,165,250,0.3); }
    .badge-retrospective { background: rgba(99,102,241,0.12); color: var(--accent2); border: 1px solid rgba(99,102,241,0.3); }
    /* 作者可信度 */
    .badge-tier1 { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-tier2 { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-tier3 { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }
    /* 逻辑有效性 */
    .badge-valid { background: rgba(34,197,94,0.12); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .badge-invalid { background: rgba(248,113,113,0.12); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }
    /* 事实来源层 */
    .badge-authoritative { background: rgba(34,197,94,0.15); color: #4ade80; border: 1px solid rgba(34,197,94,0.4); }
    .badge-mainstream_media { background: rgba(96,165,250,0.12); color: var(--blue); border: 1px solid rgba(96,165,250,0.3); }
    .badge-market_data { background: rgba(99,102,241,0.12); color: var(--accent2); border: 1px solid rgba(99,102,241,0.3); }
    .badge-rumor { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-no_source { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }
    /* 条件类型 */
    .badge-assumption { background: rgba(251,191,36,0.12); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }
    .badge-implicit { background: rgba(107,114,128,0.12); color: var(--gray); border: 1px solid rgba(107,114,128,0.3); }

    /* ── Collapsible detail ── */
    .toggle-btn { cursor: pointer; font-size: 11px; color: var(--text-dim); margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); user-select: none; display: flex; align-items: center; gap: 4px; }
    .toggle-btn::after { content: '▼'; font-size: 9px; }
    .toggle-btn.collapsed::after { content: '▶'; }
    .detail-block { padding-top: 10px; }
    .detail-text { font-size: 12px; color: var(--text-dim); line-height: 1.7; white-space: pre-wrap; word-break: break-word; margin-bottom: 6px; }
    .detail-label { font-size: 10px; font-weight: 700; color: var(--accent2); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; margin-top: 10px; }

    /* ── Condition items ── */
    .cond-list { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
    .cond-item { padding: 10px 14px; background: var(--surface2); border-radius: 8px; border-left: 3px solid var(--border); }
    .cond-item.consensus { border-left-color: var(--green); }
    .cond-item.disputed, .cond-item.non_consensus { border-left-color: var(--red); }
    .cond-item.strong_assumption { border-left-color: var(--red); }
    .cond-item.likely_assumption { border-left-color: var(--green); }
    .cond-item.unavailable, .cond-item.pending { border-left-color: var(--border); }
    .cond-text { font-size: 12px; margin-bottom: 8px; }
    .cond-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }

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

    /* ── DAG ── */
    .dag-wrap { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; background: #0d1117; position: relative; }
    #dagContainer { width: 100%; height: 480px; }
    .dag-legend { display: flex; flex-wrap: wrap; gap: 12px; padding: 10px 16px; border-top: 1px solid var(--border); background: var(--surface); }
    .dag-legend-item { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-dim); }
    .dag-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  </style>
  <script src="/static/vis-network.min.js"></script>
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

    <!-- Conditions (assumption + implicit) -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">条件</div>
        <div class="section-count" id="condsCount"></div>
      </div>
      <div class="cards-grid" id="condsGrid"></div>
    </div>

    <!-- Logic DAG -->
    <div class="section">
      <div class="section-header">
        <div class="section-title">逻辑关系图</div>
        <div class="section-count" id="dagCount"></div>
      </div>
      <div class="dag-wrap">
        <div id="dagContainer"></div>
        <div class="dag-legend">
          <div class="dag-legend-item"><div class="dag-dot" style="background:#4ade80"></div>事实（可信）</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#fbbf24"></div>事实（宽泛/小道）</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#f87171"></div>事实（不可信）</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#6b7280"></div>事实（无数据）</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#c084fc;border-radius:2px"></div>结论</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#f59e0b;border-radius:2px"></div>条件</div>
          <div class="dag-legend-item"><div class="dag-dot" style="background:#60a5fa;border-radius:2px"></div>解决方案</div>
        </div>
      </div>
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
              <th style="width:30%">结论</th>
              <th style="width:28%">支撑事实</th>
              <th style="width:20%">解决方案</th>
              <th style="width:22%">语义判定</th>
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
  {n:4,  l:"逻辑验证"},
  {n:5,  l:"现实对齐"},
  {n:6,  l:"预测监控"},
  {n:7,  l:"裁定推导"},
  {n:8,  l:"角色评估"},
  {n:9,  l:"质量评估"},
  {n:10, l:"统计更新"},
  {n:11, l:"汇总结果"},
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

// 通用裁定标签
const VERDICT_LABELS = {
  confirmed: '✓ 已确认', refuted: '✗ 已驳斥',
  partial: '△ 部分支持', unverifiable: '— 无法核实', pending: '… 待裁定',
};
// v2.2 实体语义判定标签
const FACT_VERDICT_LABELS = {
  credible: '✓ 可信', vague: '~ 宽泛', unreliable: '✗ 不可信', unavailable: '— 无数据',
};
const CONC_RETRO_LABELS = {
  credible: '✓ 可信', vague: '~ 宽泛', unreliable: '✗ 不可信', unavailable: '— 无数据',
};
const CONC_PRED_LABELS = {
  pending: '… 等待中', accurate: '✓ 准确', directional: '→ 方向一致',
  off_target: '△ 误差较大', wrong: '✗ 错误',
};
const COND_ASSUMPTION_LABELS = {
  strong_assumption: '⚠ 强假设', likely_assumption: '✓ 较大概率', unavailable: '— 无数据',
};
const COND_IMPLICIT_LABELS = {
  consensus: '✓ 共识', non_consensus: '~ 非共识', disputed: '✗ 有争议', unavailable: '— 无数据',
};
const CONF_LABELS = { certain:'确定', likely:'可能', uncertain:'不确定', speculative:'猜测' };
const TIER_LABELS = { 1:'顶级权威', 2:'行业专家', 3:'知名评论员', 4:'普通KOL', 5:'未知' };
const LOGIC_VALIDITY_LABELS = { valid:'✓ 逻辑有效', partial:'~ 部分有效', invalid:'✗ 逻辑无效' };
const SOURCE_TIER_LABELS = {
  authoritative:   '权威来源',
  mainstream_media:'主流媒体',
  market_data:     '金融市场',
  rumor:           '小道消息',
  no_source:       '无消息源',
};

function badge(cls, text) {
  return `<span class="badge badge-${cls}">${text}</span>`;
}
function verdictBadge(v) {
  if (!v) return badge('pending', '待裁定');
  const key = String(v).replace(/[^a-z_]/g, '');
  return badge(key, VERDICT_LABELS[v] || v);
}
function factVerdictBadge(v) {
  if (!v) return badge('pending', '待判定');
  const key = String(v).replace(/[^a-z_]/g, '');
  return badge(key, FACT_VERDICT_LABELS[v] || v);
}
function concVerdictBadge(c) {
  if (c.conclusion_type === 'predictive') {
    const v = c.prediction_verdict;
    if (!v) return badge('pending', '等待中');
    const key = String(v).replace(/[^a-z_]/g, '');
    return badge(key, CONC_PRED_LABELS[v] || v);
  } else {
    const v = c.conclusion_verdict;
    if (!v) return badge('pending', '待判定');
    const key = String(v).replace(/[^a-z_]/g, '');
    return badge(key, CONC_RETRO_LABELS[v] || v);
  }
}
function condVerdictBadge(cond) {
  const v = cond.condition_verdict;
  if (!v) return badge('pending', '待判定');
  const key = String(v).replace(/[^a-z_]/g, '');
  if (cond.condition_type === 'assumption') {
    return badge(key, COND_ASSUMPTION_LABELS[v] || v);
  } else {
    return badge(key, COND_IMPLICIT_LABELS[v] || v);
  }
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


// ─── Render all ───────────────────────────────────────────────────────────────

// ─── Logic DAG ────────────────────────────────────────────────────────────────

let _dagNetwork = null;

function renderDAG(data) {
  if (typeof vis === 'undefined') {
    document.getElementById('dagCount').textContent = '（vis.js 未加载）';
    document.getElementById('dagContainer').innerHTML =
      '<div style="padding:40px;text-align:center;color:var(--text-dim);font-size:13px;">逻辑关系图需要 vis-network 库，当前网络环境无法加载，其他功能不受影响。</div>';
    return;
  }

  const facts   = data.facts   || [];
  const conds   = data.conditions || [];
  const concls  = data.conclusions || [];

  const nodes = new vis.DataSet();
  const edges = new vis.DataSet();
  const addedNodes = new Set();

  function addNode(id, opts) {
    if (!addedNodes.has(id)) { nodes.add({id, ...opts}); addedNodes.add(id); }
  }

  // ── Fact nodes ──
  facts.forEach(f => {
    const col = f.fact_verdict === 'credible'    ? '#4ade80'
              : f.fact_verdict === 'vague'        ? '#fbbf24'
              : f.fact_verdict === 'unreliable'   ? '#f87171'
              : '#6b7280';
    const lbl = 'F' + f.id + ': ' + (f.claim||'').substring(0,30) + ((f.claim||'').length>30?'…':'');
    addNode('f'+f.id, {
      label: lbl, shape: 'box', level: 0,
      color: { background: col+'22', border: col, highlight: { background: col+'44', border: col } },
      font: { color: '#e2e8f0', size: 11, multi: false },
      margin: 8,
    });
  });

  // ── Condition nodes ──
  conds.forEach(c => {
    const col = c.condition_type === 'assumption' ? '#f59e0b' : '#94a3b8';
    const lbl = 'C' + c.id + ': ' + (c.condition_text||'').substring(0,30) + ((c.condition_text||'').length>30?'…':'');
    addNode('c'+c.id, {
      label: lbl, shape: 'diamond', level: 0,
      color: { background: col+'22', border: col, highlight: { background: col+'44', border: col } },
      font: { color: '#e2e8f0', size: 11 },
      margin: 8,
    });
  });

  // ── Conclusion + Solution nodes & edges ──
  const solAdded = new Set();
  concls.forEach(conc => {
    const isCore  = conc.is_core_conclusion;
    const isCycle = conc.is_in_cycle;
    const border  = isCycle ? '#f87171' : isCore ? '#a78bfa' : '#818cf8';
    const bg      = isCore  ? '#7c3aed' : '#6366f1';
    const lbl     = (isCore ? '★ ' : '') + 'Conc' + conc.id + ': ' + (conc.claim||'').substring(0,28) + ((conc.claim||'').length>28?'…':'');
    addNode('conc'+conc.id, {
      label: lbl, shape: 'ellipse', level: 1,
      color: { background: bg+'22', border: border, highlight: { background: bg+'44', border: border } },
      font: { color: '#e2e8f0', size: 11, bold: isCore },
      margin: 10,
    });

    // edges: facts → conclusion
    (conc.supporting_facts || []).forEach(f => {
      edges.add({ from: 'f'+f.id, to: 'conc'+conc.id, arrows: 'to',
        color: { color: '#4b5563', highlight: '#818cf8' }, smooth: { type: 'curvedCW', roundness: 0.1 } });
    });
    // edges: conditions → conclusion
    (conc.supporting_condition_ids || []).forEach(cid => {
      edges.add({ from: 'c'+cid, to: 'conc'+conc.id, arrows: 'to', dashes: true,
        color: { color: '#4b5563', highlight: '#f59e0b' }, smooth: { type: 'curvedCW', roundness: 0.1 } });
    });
    // edges: conclusion → conclusion
    (conc.supporting_conclusion_ids || []).forEach(cid => {
      edges.add({ from: 'conc'+cid, to: 'conc'+conc.id, arrows: 'to',
        color: { color: '#6366f1', highlight: '#a78bfa' }, smooth: { type: 'curvedCW', roundness: 0.15 } });
    });
    // solution nodes + edges
    (conc.solutions || []).forEach(s => {
      if (!solAdded.has(s.id)) {
        const lbl = 'Sol' + s.id + ': ' + (s.action_target||s.claim||'').substring(0,28) + ((s.action_target||s.claim||'').length>28?'…':'');
        addNode('sol'+s.id, {
          label: lbl, shape: 'box', level: 2,
          color: { background: '#1e3a5f', border: '#60a5fa', highlight: { background: '#1e40af', border: '#93c5fd' } },
          font: { color: '#e2e8f0', size: 11 },
          margin: 8,
        });
        solAdded.add(s.id);
      }
      edges.add({ from: 'conc'+conc.id, to: 'sol'+s.id, arrows: 'to',
        color: { color: '#1d4ed8', highlight: '#60a5fa' }, smooth: { type: 'curvedCW', roundness: 0.1 } });
    });
  });

  const total = facts.length + conds.length + concls.length + solAdded.size;
  document.getElementById('dagCount').textContent = total + ' 个节点';

  const container = document.getElementById('dagContainer');
  if (_dagNetwork) { _dagNetwork.destroy(); }
  _dagNetwork = new vis.Network(container, { nodes, edges }, {
    layout: {
      hierarchical: {
        enabled: true,
        direction: 'LR',
        sortMethod: 'directed',
        levelSeparation: 220,
        nodeSpacing: 120,
        treeSpacing: 180,
        blockShifting: true,
        edgeMinimization: true,
        parentCentralization: true,
      },
    },
    physics: { enabled: false },
    interaction: { hover: true, tooltipDelay: 200, navigationButtons: false, zoomView: true },
    edges: {
      width: 1.5,
      selectionWidth: 2.5,
      smooth: { enabled: true },
    },
    nodes: { borderWidth: 1.5, borderWidthSelected: 2.5 },
    background: '#0d1117',
  });
}

function renderAll(data) {
  document.getElementById('resultsSection').classList.add('visible');
  renderFacts(data.facts || []);
  renderConditions(data.conditions || []);
  try { renderDAG(data); } catch(e) {
    console.error('DAG render error:', e);
    document.getElementById('dagContainer').innerHTML =
      '<div style="padding:40px;text-align:center;color:var(--text-dim);font-size:13px;">逻辑关系图渲染失败：' + e.message + '</div>';
  }
  renderConclusions(data.conclusions || [], data.facts || [], data.conditions || []);

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
    const hasDetail = f.alignment_evidence || (f.refs && f.refs.length);
    const temporalNote = f.temporal_note
      ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">⏱ ${esc(f.temporal_note)}</div>`
      : '';
    return `
<div class="card">
  <div class="card-header">
    <div class="card-id">F${f.id}</div>
    <div class="card-claim">${esc(f.claim)}</div>
  </div>
  <div class="card-meta">
    ${f.fact_source_tier ? badge(f.fact_source_tier, SOURCE_TIER_LABELS[f.fact_source_tier] || f.fact_source_tier) : ''}
    ${factVerdictBadge(f.fact_verdict)}
    ${!f.is_verifiable ? badge('unavailable','不可核实') : ''}
  </div>
  ${temporalNote}
  ${hasDetail ? `
  <div class="toggle-btn collapsed" onclick="toggleDetail('${detailId}',this)">核查详情</div>
  <div id="${detailId}" style="display:none;" class="detail-block">
    ${f.alignment_evidence ? `<div class="detail-label">核查摘要</div><div class="detail-text">${esc(f.alignment_evidence)}</div>` : ''}
    ${f.verifiable_statement ? `<div class="detail-label">可验证陈述</div><div class="detail-text">${esc(f.verifiable_statement)}</div>` : ''}
    ${(f.refs && f.refs.length) ? `<div class="detail-label">参考来源</div>${f.refs.map(r => `<div class="detail-text">📌 [${esc(r.org)}] ${esc(r.desc)}${r.url ? ' — <a href="'+esc(r.url)+'" target="_blank" style="font-size:11px;">链接</a>' : ''}</div>`).join('')}` : ''}
  </div>` : ''}
</div>`;
  }).join('');
}

// ─── Conditions (assumption + implicit) ──────────────────────────────────────

function renderConditions(conds) {
  document.getElementById('condsCount').textContent = conds.length + ' 条';
  const grid = document.getElementById('condsGrid');
  if (!conds.length) {
    grid.innerHTML = '<div style="color:var(--text-dim);padding:8px;">无条件</div>';
    return;
  }
  grid.innerHTML = conds.map(c => {
    const typeLabel = c.condition_type === 'assumption' ? '假设条件' : '隐含条件';
    const detailId = 'cd' + c.id;
    const hasDetail = c.alignment_evidence || c.verifiable_statement;
    return `
<div class="card">
  <div class="card-header">
    <div class="card-id">C${c.id}</div>
    <div class="card-claim">${esc(c.condition_text)}</div>
  </div>
  <div class="card-meta">
    ${badge(c.condition_type, typeLabel)}
    ${condVerdictBadge(c)}
  </div>
  ${c.temporal_note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">⏱ ${esc(c.temporal_note)}</div>` : ''}
  ${hasDetail ? `
  <div class="toggle-btn collapsed" onclick="toggleDetail('${detailId}',this)">详情</div>
  <div id="${detailId}" style="display:none;" class="detail-block">
    ${c.alignment_evidence ? `<div class="detail-label">判定依据</div><div class="detail-text">${esc(c.alignment_evidence)}</div>` : ''}
    ${c.verifiable_statement ? `<div class="detail-label">可验证陈述</div><div class="detail-text">${esc(c.verifiable_statement)}</div>` : ''}
  </div>` : ''}
</div>`;
  }).join('');
}

function condItemInline(cond) {
  const typeLabel = cond.condition_type === 'assumption' ? '假设' : '隐含';
  return `
<div class="cond-item ${cond.condition_verdict || 'pending'}">
  <div class="cond-text">${esc(cond.condition_text)}</div>
  <div class="cond-meta">
    ${badge(cond.condition_type, typeLabel)}
    ${condVerdictBadge(cond)}
  </div>
</div>`;
}

// ─── Conclusions Table ────────────────────────────────────────────────────────

function renderConclusions(conclusions, allFacts, allConds) {
  document.getElementById('conclusionsCount').textContent = conclusions.length + ' 个';
  const body = document.getElementById('conclusionsBody');
  if (!conclusions.length) {
    body.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-dim);padding:40px;">无结论</td></tr>';
    return;
  }

  // Build condition map for lookup by id
  const condMap = {};
  (allConds || []).forEach(cond => { condMap[cond.id] = cond; });

  body.innerHTML = conclusions.map((c, ci) => {
    // ── Col 1: Conclusion ──
    const typeLabel = c.conclusion_type === 'predictive' ? '预测型' : '回顾型';
    const confLabel = c.author_confidence ? (CONF_LABELS[c.author_confidence] || c.author_confidence) : null;
    const coreTag = c.is_core_conclusion ? `<span style="font-size:11px;color:var(--yellow);font-weight:700;" title="核心结论（逻辑链终点）">★ 核心</span>` : '';
    const cycleTag = c.is_in_cycle ? `<span style="font-size:11px;color:var(--red);font-weight:700;" title="存在循环引用，跳过裁定">⚠ 循环</span>` : '';
    const col1 = `
<div class="conc-claim-text">${esc(c.claim)}</div>
<div class="conc-meta-row">
  ${badge(c.conclusion_type, typeLabel)}
  ${confLabel ? badge(c.author_confidence, confLabel) : ''}
  ${coreTag}${cycleTag}
</div>
${c.author_confidence_note ? `<div class="conc-quote">"${esc(c.author_confidence_note)}"</div>` : ''}
${c.temporal_note || c.time_horizon_note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">⏱ ${esc(c.temporal_note || c.time_horizon_note)}</div>` : ''}
${c.chain_summary ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px;">💡 ${esc(c.chain_summary)}</div>` : ''}`;

    // ── Col 2: Supporting Facts + Conditions ──
    const sfItems = (c.supporting_facts || []).map(f => `
<div class="fact-ref role-supporting">
  <div class="fact-ref-header">
    <span style="font-size:10px;color:var(--text-dim);font-family:monospace;">F${f.id}</span>
    ${f.fact_source_tier ? badge(f.fact_source_tier, SOURCE_TIER_LABELS[f.fact_source_tier] || f.fact_source_tier) : ''}
    ${factVerdictBadge(f.fact_verdict)}
  </div>
  <div class="fact-ref-claim">${esc(f.claim)}</div>
</div>`);
    const condItems = (c.supporting_condition_ids || []).map(cid => {
      const cond = condMap[cid];
      return cond ? condItemInline(cond) : '';
    }).filter(Boolean);
    const allCol2 = sfItems.concat(condItems.length ? [`<div class="cond-list">${condItems.join('')}</div>`] : []);
    const col2 = allCol2.length ? allCol2.join('') : `<span style="color:var(--text-dim);font-size:12px;">无关联事实</span>`;

    // ── Col 3: Solutions ──
    const solItems = (c.solutions || []).map(s => `
<div class="sol-ref">
  <div class="sol-ref-header">
    ${s.action_type ? `<span class="sol-action-tag">${s.action_type.toUpperCase()}</span>` : ''}
    <span style="font-size:13px;font-weight:600;">${esc(s.action_target || '')}</span>
    ${s.verdict ? verdictBadge(s.verdict) : ''}
  </div>
  <div class="sol-claim">${esc(s.claim)}</div>
  ${s.baseline_value ? `<div class="sol-baseline">基准：${esc(s.baseline_metric || '')} = <strong>${esc(s.baseline_value)}</strong></div>` : ''}
  ${s.monitoring_period_note ? `<div style="font-size:11px;color:var(--text-dim);margin-top:4px;">📅 ${esc(s.monitoring_period_note)}</div>` : ''}
</div>`);
    const col3 = solItems.length ? solItems.join('') : `<span style="color:var(--text-dim);font-size:12px;">无解决方案</span>`;

    // ── Col 4: Semantic Verdict ──
    const col4 = (() => {
      if (c.is_in_cycle) {
        return `<div class="verdict-cell"><span style="color:var(--red);font-size:12px;">⚠ 跳过（循环）</span></div>`;
      }
      const semanticBadge = concVerdictBadge(c);
      const overallBadge = c.verdict && c.verdict !== 'null' ? verdictBadge(c.verdict) : '';
      const lvKey = c.logic_validity;
      const lvLabel = lvKey ? (LOGIC_VALIDITY_LABELS[lvKey] || lvKey) : '';
      const lvColor = lvKey === 'valid' ? 'var(--green)' : lvKey === 'invalid' ? 'var(--red)' : 'var(--yellow)';
      return `
<div class="verdict-cell">
  <div class="verdict-main">${semanticBadge}</div>
  <div class="verdict-sub">
    ${overallBadge ? `<div class="verdict-sub-row">${overallBadge}</div>` : ''}
    ${lvLabel ? `<div class="verdict-sub-row" style="color:${lvColor};font-size:11px;">${esc(lvLabel)}</div>` : ''}
  </div>
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
