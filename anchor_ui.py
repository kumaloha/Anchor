"""
Anchor UI v5 — 政策与市场分析 Web 界面
=======================================
单文件 FastAPI 应用，支持政策模式和标准模式的结果查看与 DEBUG 调试。

启动：
    DATABASE_URL="sqlite+aiosqlite:///./anchor_v4_test.db" python anchor_ui.py
"""

import asyncio
import json
import os
import uuid
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")

from anchor.database.session import AsyncSessionLocal, create_tables
from anchor.models import (
    Assumption,
    Conclusion,
    EntityRelationship,
    Fact,
    ImplicitCondition,
    Policy,
    PolicyItem,
    PolicyMeasure,
    PolicyTheme,
    Prediction,
    RawPost,
    Solution,
)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Anchor UI v5")

_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/static", StaticFiles(directory=_static), name="static")


@app.on_event("startup")
async def _startup():
    await create_tables()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mode(post: RawPost) -> str:
    return "policy" if (post.content_type or "") in {"政策宣布", "政策解读"} else "standard"


# ── API: post list ─────────────────────────────────────────────────────────────

@app.get("/api/posts")
async def api_posts():
    async with AsyncSessionLocal() as s:
        posts = list((await s.exec(select(RawPost).order_by(RawPost.id.desc()))).all())
    return [
        {
            "id": p.id,
            "author_name": p.author_name or "未知",
            "source": p.source or "",
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "content_type": p.content_type or "",
            "content_topic": p.content_topic or "",
            "content_mode": _mode(p),
            "is_processed": bool(p.is_processed),
            "chain2_analyzed": bool(p.chain2_analyzed),
        }
        for p in posts
    ]


# ── API: post detail ───────────────────────────────────────────────────────────

@app.get("/api/post/{post_id}")
async def api_post_detail(post_id: int):
    async with AsyncSessionLocal() as s:
        post = (await s.exec(select(RawPost).where(RawPost.id == post_id))).first()
        if not post:
            return JSONResponse({"error": "not found"}, status_code=404)

        mode = _mode(post)
        # Fallback：若 content_type 未保存但 policies 表已有记录，仍作 policy 模式渲染
        if mode != "policy":
            pol_count = (await s.exec(
                select(Policy).where(Policy.raw_post_id == post_id)
            )).first()
            if pol_count is not None:
                mode = "policy"
        result: Dict[str, Any] = {
            "post": {
                "id": post.id,
                "url": post.url or "",
                "author_name": post.author_name or "未知",
                "source": post.source or "",
                "posted_at": post.posted_at.isoformat() if post.posted_at else None,
                "content": (post.content or "")[:3000],
                "content_type": post.content_type or "",
                "content_type_secondary": post.content_type_secondary or "",
                "content_topic": post.content_topic or "",
                "author_intent": post.author_intent or "",
                "intent_note": post.intent_note or "",
                "issuing_authority": post.issuing_authority or "",
                "authority_level": post.authority_level or "",
                "content_summary": post.content_summary or "",
                "is_processed": bool(post.is_processed),
                "chain2_analyzed": bool(post.chain2_analyzed),
            },
            "mode": mode,
        }

        if mode == "policy":
            # ── v3 新实体（policies + policy_measures）──
            policies_v3 = list((await s.exec(
                select(Policy).where(Policy.raw_post_id == post_id)
            )).all())
            measures_all = list((await s.exec(
                select(PolicyMeasure).where(PolicyMeasure.raw_post_id == post_id)
            )).all())
            by_policy: Dict[int, list] = {}
            for m in measures_all:
                by_policy.setdefault(m.policy_id, []).append(m)

            if policies_v3:
                result["policies"] = [
                    {
                        "id": p.id,
                        "theme": p.theme,
                        "change_summary": p.change_summary or "",
                        "target": p.target or "",
                        "target_prev": p.target_prev or "",
                        "intensity": p.intensity or "",
                        "intensity_prev": p.intensity_prev or "",
                        "intensity_note": p.intensity_note or "",
                        "intensity_note_prev": p.intensity_note_prev or "",
                        "background": p.background or "",
                        "background_prev": p.background_prev or "",
                        "organization": p.organization or "",
                        "organization_prev": p.organization_prev or "",
                        "measures": [
                            {
                                "id": m.id,
                                "summary": m.summary,
                                "measure_text": m.measure_text,
                                "trend": m.trend or "",
                                "trend_note": m.trend_note or "",
                            }
                            for m in by_policy.get(p.id, [])
                        ],
                    }
                    for p in policies_v3
                ]

            # ── v2 旧实体（themes + items，旧数据兼容）──
            themes = list((await s.exec(
                select(PolicyTheme).where(PolicyTheme.raw_post_id == post_id)
            )).all())
            items_all = list((await s.exec(
                select(PolicyItem).where(PolicyItem.raw_post_id == post_id)
            )).all())
            by_theme: Dict[int, list] = {}
            for it in items_all:
                by_theme.setdefault(it.policy_theme_id, []).append(it)

            result["themes"] = [
                {
                    "id": t.id,
                    "theme_name": t.theme_name,
                    "background": t.background or "",
                    "enforcement_note": t.enforcement_note or "",
                    "has_enforcement_teeth": bool(t.has_enforcement_teeth),
                    "items": [
                        {
                            "id": it.id,
                            "summary": it.summary or "",
                            "policy_text": it.policy_text or "",
                            "urgency": it.urgency or "",
                            "is_hard_target": bool(it.is_hard_target),
                            "metric_value": it.metric_value or "",
                            "target_year": it.target_year or "",
                            "change_type": it.change_type or "",
                            "change_note": it.change_note or "",
                            "execution_status": it.execution_status or "",
                            "execution_note": it.execution_note or "",
                        }
                        for it in by_theme.get(t.id, [])
                    ],
                }
                for t in themes
            ]

            facts = list((await s.exec(select(Fact).where(Fact.raw_post_id == post_id))).all())
            result["facts"] = [
                {
                    "id": f.id,
                    "summary": f.summary or "",
                    "claim": f.claim or "",
                    "fact_verdict": f.fact_verdict or "",
                    "verdict_evidence": f.verdict_evidence or "",
                }
                for f in facts
            ]

            concs = list((await s.exec(
                select(Conclusion).where(Conclusion.raw_post_id == post_id)
            )).all())
            result["conclusions"] = [
                {
                    "id": c.id,
                    "summary": c.summary or "",
                    "claim": c.claim or "",
                    "conclusion_verdict": c.conclusion_verdict or "",
                    "is_core_conclusion": bool(c.is_core_conclusion),
                    "is_in_cycle": bool(c.is_in_cycle),
                    "author_confidence": c.author_confidence or "",
                }
                for c in concs
            ]

        else:  # standard
            facts = list((await s.exec(select(Fact).where(Fact.raw_post_id == post_id))).all())
            result["facts"] = [
                {
                    "id": f.id,
                    "summary": f.summary or "",
                    "claim": f.claim or "",
                    "fact_verdict": f.fact_verdict or "",
                    "verdict_evidence": f.verdict_evidence or "",
                }
                for f in facts
            ]

            assumptions = list((await s.exec(
                select(Assumption).where(Assumption.raw_post_id == post_id)
            )).all())
            result["assumptions"] = [
                {
                    "id": a.id,
                    "summary": a.summary or "",
                    "condition_text": a.condition_text or "",
                    "assumption_verdict": a.assumption_verdict or "",
                    "verdict_evidence": a.verdict_evidence or "",
                }
                for a in assumptions
            ]

            implicits = list((await s.exec(
                select(ImplicitCondition).where(ImplicitCondition.raw_post_id == post_id)
            )).all())
            result["implicit_conditions"] = [
                {
                    "id": ic.id,
                    "summary": ic.summary or "",
                    "condition_text": ic.condition_text or "",
                    "implicit_verdict": ic.implicit_verdict or "",
                    "verdict_evidence": ic.verdict_evidence or "",
                }
                for ic in implicits
            ]

            concs = list((await s.exec(
                select(Conclusion).where(Conclusion.raw_post_id == post_id)
            )).all())
            result["conclusions"] = [
                {
                    "id": c.id,
                    "summary": c.summary or "",
                    "claim": c.claim or "",
                    "conclusion_verdict": c.conclusion_verdict or "",
                    "is_core_conclusion": bool(c.is_core_conclusion),
                    "is_in_cycle": bool(c.is_in_cycle),
                    "author_confidence": c.author_confidence or "",
                }
                for c in concs
            ]

            preds = list((await s.exec(
                select(Prediction).where(Prediction.raw_post_id == post_id)
            )).all())
            result["predictions"] = [
                {
                    "id": p.id,
                    "summary": p.summary or "",
                    "claim": p.claim or "",
                    "prediction_verdict": p.prediction_verdict or "",
                    "temporal_validity": p.temporal_validity or "",
                    "temporal_note": p.temporal_note or "",
                }
                for p in preds
            ]

            sols = list((await s.exec(
                select(Solution).where(Solution.raw_post_id == post_id)
            )).all())
            result["solutions"] = [
                {
                    "id": s.id,
                    "summary": s.summary or "",
                    "claim": s.claim or "",
                    "action_type": s.action_type or "",
                    "action_target": s.action_target or "",
                }
                for s in sols
            ]

            edges = list((await s.exec(
                select(EntityRelationship).where(EntityRelationship.raw_post_id == post_id)
            )).all())
            result["edges"] = [
                {
                    "source_type": e.source_type,
                    "source_id": e.source_id,
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                    "edge_type": e.edge_type,
                }
                for e in edges
            ]

    return result


# ── Analyze pipeline ───────────────────────────────────────────────────────────

_tasks: Dict[str, asyncio.Queue] = {}


@app.post("/api/analyze")
async def api_analyze(req: Request):
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "url required"}, status_code=400)
    task_id = str(uuid.uuid4())
    q: asyncio.Queue = asyncio.Queue()
    _tasks[task_id] = q
    asyncio.create_task(_run_pipeline(url, q))
    return {"task_id": task_id}


async def _emit(q: asyncio.Queue, event: str, data: Any):
    await q.put({"event": event, "data": data})


async def _run_pipeline(url: str, q: asyncio.Queue):
    try:
        from anchor.chains.chain2_author import run_chain2
        from anchor.collect.input_handler import process_url
        from anchor.extract.extractor import Extractor

        extractor = Extractor()

        await _emit(q, "step", {"num": 1, "label": "采集内容"})
        async with AsyncSessionLocal() as s:
            result = await process_url(url, s)
        if not result or not result.raw_posts:
            await _emit(q, "error", "采集失败，未生成帖子")
            return
        rp = result.raw_posts[0]
        await _emit(q, "step_done", {"num": 1, "detail": f"post_id={rp.id}"})

        await _emit(q, "step", {"num": 2, "label": "Chain 2 — 内容分类 + 立场分析"})
        async with AsyncSessionLocal() as s:
            pre = await run_chain2(rp.id, s)  # Steps 1+2+3（含立场聚合），内部自动 commit
        ct = pre.get("content_type", "")
        content_mode = "policy" if ct in {"政策宣布", "政策解读"} else "standard"
        await _emit(q, "step_done", {"num": 2, "detail": f"{ct} / {pre.get('author_intent', '')} / 立场={pre.get('stance_label', '—')}"})

        await _emit(q, "step", {"num": 3, "label": "Chain 1 — 实体提取"})
        async with AsyncSessionLocal() as s:
            rp3 = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
            await extractor.extract(rp3, s, content_mode=content_mode,
                                    author_intent=pre.get("author_intent"))
        await _emit(q, "step_done", {"num": 3, "detail": "提取完成"})

        from anchor.config import settings as _settings
        if content_mode != "policy" and _settings.enable_chain3:
            # 政策模式：同比对比已内嵌于 Chain 1，Chain 3 暂缓
            # enable_chain3=False（默认）可在调试时跳过验证
            await _emit(q, "step", {"num": 4, "label": "Chain 3 — 实体验证"})
            from anchor.chains.chain3_verifier import run_chain3
            async with AsyncSessionLocal() as s:
                await run_chain3(rp.id, s)
            await _emit(q, "step_done", {"num": 4, "detail": "验证完成"})

        # ── Notion 同步 ──────────────────────────────────────────────────────
        try:
            from anchor.notion_sync import sync_post_to_notion
            async with AsyncSessionLocal() as s:
                notion_url = await sync_post_to_notion(rp.id, s)
            if notion_url:
                await _emit(q, "step_done", {"num": 5, "label": "Notion 同步", "detail": notion_url})
        except Exception as _ne:
            import logging
            logging.getLogger(__name__).warning("notion_sync error: %s", _ne)

        detail = await api_post_detail(rp.id)
        await _emit(q, "done", detail)

    except Exception as e:
        import traceback
        await _emit(q, "error", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")


@app.get("/stream/{task_id}")
async def stream(task_id: str):
    q = _tasks.get(task_id)
    if not q:
        return JSONResponse({"error": "task not found"}, status_code=404)

    async def generator():
        while True:
            msg = await q.get()
            event = msg["event"]
            data = json.dumps(msg["data"], ensure_ascii=False)
            yield f"event: {event}\ndata: {data}\n\n"
            if event in ("done", "error"):
                _tasks.pop(task_id, None)
                break

    return StreamingResponse(generator(), media_type="text/event-stream")


# ── HTML ───────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⚓ Anchor</title>
<style>
:root {
  --bg:#0d1117; --surface:#161b22; --surface2:#21262d; --surface3:#2d333b;
  --border:#30363d; --text:#e6edf3; --text2:#7d8590; --text3:#8b949e;
  --accent:#58a6ff; --green:#3fb950; --red:#f85149; --yellow:#d29922;
  --orange:#e3b341; --purple:#a371f7; --blue:#79c0ff;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);font-size:14px;height:100vh;
  display:flex;flex-direction:column;overflow:hidden}
header{background:var(--surface);border-bottom:1px solid var(--border);
  padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:10}
.logo{font-size:17px;font-weight:700;white-space:nowrap;letter-spacing:-.3px}
.logo em{color:var(--accent);font-style:normal}
.url-form{display:flex;flex:1;gap:8px;max-width:680px}
.url-input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;
  padding:6px 12px;color:var(--text);font-size:13px;transition:border-color .15s}
.url-input:focus{outline:none;border-color:var(--accent)}
.btn-primary{background:var(--accent);color:#0d1117;border:none;border-radius:6px;
  padding:6px 16px;cursor:pointer;font-weight:600;font-size:13px;white-space:nowrap}
.btn-primary:hover{opacity:.9}
.debug-wrap{display:flex;align-items:center;gap:7px;cursor:pointer;margin-left:auto;white-space:nowrap}
.debug-wrap input{display:none}
.toggle-track{width:34px;height:18px;background:var(--surface3);border:1px solid var(--border);
  border-radius:9px;position:relative;transition:.2s}
.toggle-thumb{position:absolute;left:2px;top:2px;width:12px;height:12px;
  background:var(--text2);border-radius:50%;transition:.2s}
.debug-on .toggle-track{background:var(--accent);border-color:var(--accent)}
.debug-on .toggle-thumb{left:18px;background:#fff}
.debug-label{font-size:11px;color:var(--text2);font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.debug-on .debug-label{color:var(--accent)}
#progressBar{background:var(--surface);border-bottom:1px solid var(--border);
  padding:8px 16px;display:none;gap:6px;align-items:center;overflow-x:auto;flex-shrink:0}
#progressBar.show{display:flex}
.spill{display:flex;align-items:center;gap:4px;padding:3px 10px;border-radius:10px;
  font-size:11px;background:var(--surface2);color:var(--text2);white-space:nowrap}
.spill.active{background:rgba(88,166,255,.12);color:var(--accent)}
.spill.done{background:rgba(63,185,80,.1);color:var(--green)}
.spill.err{background:rgba(248,81,73,.1);color:var(--red)}
.spill-dot{width:5px;height:5px;border-radius:50%;background:currentColor}
.spill.active .spill-dot{animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
.step-sep{color:var(--border);font-size:12px;flex-shrink:0}
.layout{display:flex;flex:1;overflow:hidden}
.sidebar{width:250px;background:var(--surface);border-right:1px solid var(--border);
  display:flex;flex-direction:column;flex-shrink:0}
.sidebar-hd{padding:10px 14px;font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.5px;color:var(--text2);border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center}
.sidebar-hd button{background:none;border:none;color:var(--text2);cursor:pointer;font-size:15px;padding:0 2px}
.sidebar-hd button:hover{color:var(--text)}
#postList{overflow-y:auto;flex:1}
.pi{padding:11px 14px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .1s}
.pi:hover{background:var(--surface2)}
.pi.active{background:rgba(88,166,255,.08);border-left:2px solid var(--accent);padding-left:12px}
.pi-author{font-weight:600;font-size:13px;margin-bottom:3px;display:flex;align-items:center;gap:6px}
.pi-meta{display:flex;gap:5px;flex-wrap:wrap;font-size:11px;color:var(--text2);align-items:center}
.pi-topic{font-size:11px;color:var(--text3);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dot-ok{color:var(--green);font-size:9px}
.dot-no{color:var(--text2);font-size:9px}
.main{flex:1;overflow-y:auto;padding:20px 24px}
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;color:var(--text2);gap:8px;text-align:center}
.empty-icon{font-size:44px;line-height:1}
.bd{display:inline-flex;align-items:center;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.bd-green{background:rgba(63,185,80,.12);color:var(--green)}
.bd-red{background:rgba(248,81,73,.12);color:var(--red)}
.bd-yellow{background:rgba(210,153,34,.12);color:var(--yellow)}
.bd-gray{background:rgba(125,133,144,.1);color:var(--text2)}
.bd-blue{background:rgba(121,192,255,.12);color:var(--blue)}
.bd-purple{background:rgba(163,113,247,.12);color:var(--purple)}
.bd-orange{background:rgba(227,179,65,.12);color:var(--orange)}
.bd-accent{background:rgba(88,166,255,.1);color:var(--accent);border:1px solid rgba(88,166,255,.25)}
.summary-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  padding:16px 18px;margin-bottom:18px}
.s-meta{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:8px}
.s-field{display:flex;flex-direction:column;gap:2px}
.s-label{font-size:10px;color:var(--text2);text-transform:uppercase;letter-spacing:.4px}
.s-value{font-size:13px;font-weight:500}
.s-summary{font-size:13px;color:var(--text3);line-height:1.65;padding-top:10px;
  border-top:1px solid var(--border);margin-top:6px}
.dbg-block{background:var(--surface);border:1px solid rgba(88,166,255,.2);border-radius:8px;
  padding:12px 14px;margin-bottom:14px}
.dbg-title{font-size:10px;font-weight:600;color:var(--accent);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:10px}
.dbg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.dbg-field{display:flex;flex-direction:column;gap:2px}
.dbg-label{font-size:10px;color:var(--text2);text-transform:uppercase}
.dbg-value{font-size:13px}
.dbg-note{grid-column:1/-1;font-size:12px;color:var(--text2);margin-top:4px;line-height:1.5}
.sec{margin-bottom:22px}
.sec-title{font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:10px;display:flex;align-items:center;gap:7px}
.cnt{background:var(--surface2);padding:1px 7px;border-radius:8px;font-size:11px;color:var(--text2)}
.cmp-stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.cmp-pill{display:flex;flex-direction:column;align-items:center;padding:8px 18px;
  border-radius:8px;background:var(--surface2);gap:2px}
.cmp-num{font-size:22px;font-weight:700}
.cmp-lbl{font-size:11px;color:var(--text2)}
.cmp-new{color:var(--green)}.cmp-adj{color:var(--yellow)}.cmp-con{color:var(--text2)}.cmp-del{color:var(--red)}
.theme-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  margin-bottom:10px;overflow:hidden}
.theme-hd{display:flex;align-items:center;gap:8px;padding:11px 14px;cursor:pointer;transition:background .1s}
.theme-hd:hover{background:var(--surface2)}
.theme-chev{color:var(--text2);font-size:11px;transition:transform .18s;flex-shrink:0}
.theme-card.open .theme-chev{transform:rotate(90deg)}
.theme-name{font-weight:700;font-size:17px;flex:1;color:var(--accent);
  background:rgba(88,166,255,.08);border-radius:5px;padding:2px 8px;letter-spacing:-.2px}
.theme-body{display:none;border-top:1px solid var(--border)}
.theme-card.open .theme-body{display:block}
.theme-bg{padding:10px 14px;font-size:12px;color:var(--text3);line-height:1.65;
  border-bottom:1px solid var(--border)}
.theme-bg-lbl{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;margin-bottom:3px}
.enf-note{padding:8px 14px 10px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)}
.pi-table{width:100%;border-collapse:collapse}
.pi-table th{text-align:left;padding:7px 12px;font-size:10px;font-weight:600;
  color:var(--text2);text-transform:uppercase;border-bottom:1px solid var(--border)}
.pi-table td{padding:9px 12px;vertical-align:top;border-bottom:1px solid var(--border);font-size:13px}
.pi-table tr:last-child td{border-bottom:none}
.pi-table tr:hover td{background:rgba(255,255,255,.02)}
.pi-text{font-size:11px;color:var(--text2);margin-top:4px;line-height:1.5}
.pi-note{font-size:11px;color:var(--text2);margin-top:3px}
.pi-id{font-size:10px;color:var(--border)}
.urg{padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600}
.urg-m{background:rgba(248,81,73,.1);color:#f85149}
.urg-e{background:rgba(63,185,80,.1);color:#3fb950}
.urg-p{background:rgba(121,192,255,.1);color:#79c0ff}
.urg-g{background:rgba(125,133,144,.1);color:#7d8590}
.chg{padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500}
.chg-n{background:rgba(63,185,80,.1);color:#3fb950}
.chg-a{background:rgba(210,153,34,.1);color:#d29922}
.chg-c{background:rgba(125,133,144,.08);color:#7d8590}
.chg-d{background:rgba(248,81,73,.1);color:#f85149}
.exec{font-size:12px}
.exec-note{font-size:11px;color:var(--text2);margin-top:3px;line-height:1.4}
/* Policy v3 styles */
.int{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.int-s{background:rgba(248,81,73,.1);color:#f85149}
.int-m{background:rgba(210,153,34,.1);color:#d29922}
.int-w{background:rgba(125,133,144,.08);color:#7d8590}
.trend{padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500}
.trend-up{background:rgba(63,185,80,.1);color:#3fb950}
.trend-down{background:rgba(248,81,73,.1);color:#f85149}
.trend-cont{background:rgba(125,133,144,.08);color:#7d8590}
.trend-new{background:rgba(121,192,255,.12);color:#79c0ff}
.pol-field{display:flex;gap:8px;padding:7px 14px;font-size:12px;line-height:1.55;
  border-bottom:1px solid var(--border)}
.pol-field-lbl{min-width:42px;font-size:10px;font-weight:600;text-transform:uppercase;
  color:var(--text2);padding-top:1px}
.pol-field-val{flex:1;color:var(--text3)}
.measure-list{padding:8px 14px 10px}
.measure-item{display:flex;gap:10px;align-items:flex-start;padding:5px 0;
  border-bottom:1px solid var(--border)}
.measure-item:last-child{border-bottom:none}
.measure-body{flex:1}
.measure-summary{font-size:13px;font-weight:600;margin-bottom:3px}
.measure-text{font-size:12px;color:var(--text3);line-height:1.55}
.measure-note{font-size:11px;color:var(--text2);margin-top:3px}
.eg{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:10px}
.ec{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  padding:11px 13px;transition:border-color .15s}
.ec:hover{border-color:rgba(88,166,255,.3)}
.ec-summary{font-size:13px;font-weight:500;margin-bottom:6px;line-height:1.4}
.ec-claim{font-size:12px;color:var(--text2);line-height:1.55;margin-bottom:6px;margin-top:4px}
.ec-footer{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.ec-id{font-size:10px;color:var(--border);margin-top:4px}
.ec-evidence{font-size:11px;color:var(--text2);margin-top:6px;line-height:1.45;
  padding-top:6px;border-top:1px solid var(--border)}
.del-list{background:rgba(248,81,73,.04);border:1px solid rgba(248,81,73,.18);
  border-radius:8px;padding:12px 14px}
.del-item{font-size:13px;padding:4px 0;border-bottom:1px solid rgba(248,81,73,.08)}
.del-item:last-child{border-bottom:none}
/* 变化视图切换 */
.view-toggle{display:flex;gap:4px;margin-left:auto}
.view-btn{background:none;border:1px solid var(--border);color:var(--text2);
  border-radius:5px;padding:3px 10px;font-size:11px;cursor:pointer;font-weight:500}
.view-btn.active{background:var(--accent);color:#0d1117;border-color:var(--accent);font-weight:700}
/* 变化视图 diff card */
.diff-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  margin-bottom:8px;overflow:hidden}
.diff-hd{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 14px;
  border-bottom:1px solid var(--border);cursor:pointer;user-select:none}
.diff-summary{width:100%;font-size:11px;color:var(--text2);margin-top:2px;line-height:1.4;
  padding-left:18px}
.diff-hd:hover{background:rgba(255,255,255,.02)}
.diff-chev{font-size:10px;color:var(--text2);transition:transform .15s}
.diff-card.open .diff-chev{transform:rotate(90deg)}
.diff-body{display:none;flex-direction:column}
.diff-card.open .diff-body{display:flex}
.diff-no-change{font-size:12px;color:var(--text2);padding:10px 14px;font-style:italic}
/* 两列表格 */
.diff-table{width:100%;border-collapse:collapse;font-size:12px}
.diff-table th{padding:5px 12px;font-size:10px;font-weight:700;text-transform:uppercase;
  color:var(--text2);border-bottom:1px solid var(--border);text-align:left}
.diff-table th.col-year{width:50%;border-left:1px solid var(--border)}
.diff-table td{padding:8px 12px;vertical-align:top;line-height:1.6;color:var(--text3);
  border-bottom:1px solid rgba(255,255,255,.04)}
.diff-table td.col-lbl{width:42px;font-size:10px;font-weight:700;text-transform:uppercase;
  color:var(--text2);white-space:nowrap;border-right:1px solid var(--border)}
.diff-table td.col-curr{width:50%;border-right:1px solid var(--border)}
.diff-table td.col-prev{width:50%;color:var(--text2)}
.diff-table tr:last-child td{border-bottom:none}
.diff-count{font-size:11px;color:var(--text2);margin-left:auto;white-space:nowrap}
#dag-wrap{border:1px solid var(--border);border-radius:8px;height:420px;
  background:var(--surface);overflow:hidden}
.edge-row{font-size:11px;color:var(--text2);padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.edge-type{color:var(--accent)}
.raw-content{background:var(--surface2);border:1px solid var(--border);border-radius:6px;
  padding:10px 12px;font-size:12px;color:var(--text2);line-height:1.6;
  max-height:180px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;margin-top:8px}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}

/* PDF 导出按钮 */
#pdfBtn{
  position:fixed;bottom:28px;right:28px;z-index:999;
  background:var(--accent);color:#0d1117;border:none;border-radius:8px;
  padding:9px 18px;cursor:pointer;font-weight:700;font-size:13px;
  box-shadow:0 4px 14px rgba(0,0,0,.4);display:none;
  transition:opacity .15s,transform .15s}
#pdfBtn:hover{opacity:.9;transform:translateY(-1px)}
#pdfBtn.visible{display:block}

/* 打印 / PDF 样式 */
@media print{
  header,#progressBar,.sidebar,#pdfBtn,.debug-wrap,.dbg-block{display:none!important}
  .layout{display:block}
  .main{padding:0;background:#fff;color:#000;border:none}
  body{background:#fff;color:#000}
  .summary-card,.theme-card,.sec,.eg{break-inside:avoid;background:#fff!important;border-color:#ccc!important;color:#000!important}
  .theme-card{display:block!important}
  .theme-body{display:block!important;max-height:none!important;overflow:visible!important}
  .int{color:#000!important;border:1px solid #999!important;background:#eee!important}
  .trend,.bd{color:#000!important;background:#eee!important;border:1px solid #999!important}
  .sec-title,.pol-theme,.field-label{color:#000!important}
  .field-val,.pol-target,.pol-bg,.pol-org,.pol-note{color:#111!important}
  a{color:#000!important;text-decoration:none}
}
</style>
</head>
<body>

<header>
  <div class="logo">⚓ <em>Anchor</em></div>
  <form class="url-form" onsubmit="submitAnalyze(event)">
    <input class="url-input" id="urlInput" type="text"
      placeholder="输入 URL 开始分析 (Twitter/X · 微博 · Web · PDF)…" autocomplete="off">
    <button class="btn-primary" type="submit">分析</button>
  </form>
  <label class="debug-wrap" id="debugWrap">
    <input type="checkbox" id="debugCb" onchange="toggleDebug(this)">
    <div class="toggle-track"><div class="toggle-thumb"></div></div>
    <span class="debug-label">Debug</span>
  </label>
</header>

<div id="progressBar"></div>

<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-hd">
      <span>帖子列表</span>
      <button onclick="loadPosts()" title="刷新">↻</button>
    </div>
    <div id="postList">
      <div style="padding:16px;color:var(--text2);font-size:12px;">加载中…</div>
    </div>
  </aside>

  <main class="main" id="main">
    <div class="empty">
      <div class="empty-icon">⚓</div>
      <div>选择左侧帖子查看分析结果</div>
      <div style="font-size:12px;color:var(--text2)">或输入 URL 开始新分析</div>
    </div>
  </main>
</div>

<button id="pdfBtn" onclick="exportPDF()">↓ 导出 PDF</button>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>

<script src="/static/vis-network.min.js"></script>
<script>
// ── State ──────────────────────────────────────────────────────────────────────
let debugMode = localStorage.getItem('anchorDebug') === '1';
let currentData = null;

(function init(){
  if (debugMode) {
    document.getElementById('debugCb').checked = true;
    document.getElementById('debugWrap').classList.add('debug-on');
  }
  loadPosts();
})();

function toggleDebug(cb) {
  debugMode = cb.checked;
  localStorage.setItem('anchorDebug', debugMode ? '1' : '0');
  document.getElementById('debugWrap').classList.toggle('debug-on', debugMode);
  if (currentData) renderPost(currentData);
}

// ── Post list ──────────────────────────────────────────────────────────────────
async function loadPosts() {
  const res = await fetch('/api/posts');
  const posts = await res.json();
  const el = document.getElementById('postList');
  if (!posts.length) {
    el.innerHTML = '<div style="padding:16px;color:var(--text2);font-size:12px;">暂无数据</div>';
    return;
  }
  el.innerHTML = posts.map(p => {
    const date = p.posted_at ? p.posted_at.slice(0,10) : '无日期';
    const modeBd = p.content_mode === 'policy'
      ? `<span class="bd bd-accent" style="font-size:10px;padding:1px 6px;">政策</span>`
      : `<span class="bd bd-gray" style="font-size:10px;padding:1px 6px;">标准</span>`;
    const ok = p.is_processed ? '●' : '○';
    const okCls = p.is_processed ? 'dot-ok' : 'dot-no';
    return `<div class="pi" onclick="selectPost(${p.id})" id="pi-${p.id}">
      <div class="pi-author"><span class="${okCls}">${ok}</span>${esc(p.author_name)}</div>
      <div class="pi-meta"><span>${date}</span>${p.content_type?`<span>${esc(p.content_type)}</span>`:''} ${modeBd}</div>
      ${p.content_topic?`<div class="pi-topic">${esc(p.content_topic)}</div>`:''}
    </div>`;
  }).join('');
}

async function selectPost(id) {
  document.querySelectorAll('.pi').forEach(e => e.classList.remove('active'));
  const pi = document.getElementById(`pi-${id}`);
  if (pi) pi.classList.add('active');
  document.getElementById('main').innerHTML =
    '<div style="padding:40px;color:var(--text2);text-align:center;font-size:13px;">加载中…</div>';
  const res = await fetch(`/api/post/${id}`);
  if (!res.ok) {
    document.getElementById('main').innerHTML =
      '<div style="padding:40px;color:var(--red)">加载失败</div>';
    return;
  }
  currentData = await res.json();
  renderPost(currentData);
}

// ── Render dispatcher ──────────────────────────────────────────────────────────
function renderPost(data) {
  const main = document.getElementById('main');
  if (data.mode === 'policy') {
    main.innerHTML = renderPolicy(data);
    document.querySelectorAll('.theme-hd').forEach(h => {
      h.onclick = () => h.closest('.theme-card').classList.toggle('open');
    });
    document.querySelectorAll('.theme-card').forEach(c => c.classList.add('open'));
  } else {
    main.innerHTML = renderStandard(data);
    renderDAG(data);
  }
  document.getElementById('pdfBtn').classList.add('visible');
}

function exportPDF() {
  // 展开所有折叠卡片
  document.querySelectorAll('.theme-card').forEach(c => c.classList.add('open'));
  const el = document.getElementById('main');
  const post = currentData && currentData.post;
  const topic = post && post.content_topic ? post.content_topic : 'anchor-report';
  const filename = topic.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60) + '.pdf';
  html2pdf().set({
    margin: [12, 10, 12, 10],
    filename,
    image: {type: 'jpeg', quality: 0.95},
    html2canvas: {scale: 2, useCORS: true, backgroundColor: '#ffffff'},
    jsPDF: {unit: 'mm', format: 'a4', orientation: 'portrait'},
    pagebreak: {mode: ['avoid-all', 'css']}
  }).from(el).save();
}

// ══════════════════════════════════════════════════════════════════════════════
// POLICY MODE
// ══════════════════════════════════════════════════════════════════════════════

let policyView = 'diff'; // 'diff' | 'full'

function setPolicyView(mode) {
  policyView = mode;
  document.querySelectorAll('.view-btn').forEach(b => b.classList.toggle('active', b.dataset.v === mode));
  if (currentData) renderPost(currentData);
}

// 高亮：数字（蓝）、强硬语气词（红）、方向词（橙）、组织变更（绿）
function hilightField(text, type) {
  if (!text) return '<span style="color:var(--text2)">—</span>';
  let s = esc(text);
  if (type === 'target') {
    s = s.replace(
      /(\d+(?:\.\d+)?(?:万亿|亿|万|千亿|%|百分点|个百分点|元|倍|次|年))/g,
      '<span style="color:#79c0ff;font-weight:600">$1</span>'
    );
  }
  if (type === 'intensity') {
    s = s.replace(
      /(坚决|必须|严禁|严格|不得|绝不|强化|大力|加快|超常规|历史性)/g,
      '<span style="color:#f85149;font-weight:600">$1</span>'
    );
    s = s.replace(
      /(扩张|积极|稳健|宽松|收紧|由.{1,8}(?:转为|改为|升级为)|升级|降级)/g,
      '<span style="color:#d29922;font-weight:600">$1</span>'
    );
  }
  if (type === 'org') {
    s = s.replace(
      /(新增|新设|调整|纳入考核|问责|联合|牵头|统筹)/g,
      '<span style="color:#3fb950;font-weight:600">$1</span>'
    );
  }
  return s;
}

// 变化视图 diff card — 两列对比表格
function renderPolicyDiffCard(pol) {
  const intMap = {
    strong:['int int-s','强'], moderate:['int int-m','中'], weak:['int int-w','宽']
  };
  const [intCls, intLbl] = intMap[pol.intensity] || ['int int-w', pol.intensity||'—'];
  const [prevCls, prevLbl] = intMap[pol.intensity_prev] || ['int int-w', pol.intensity_prev||'—'];

  const hasPrev = !!(pol.target_prev || pol.background_prev || pol.organization_prev || pol.intensity_prev);
  const intensityChanged = pol.intensity && pol.intensity_prev && pol.intensity !== pol.intensity_prev;

  // 判断是否有实质变化（任一字段不同）
  const hasChange = intensityChanged ||
    (pol.intensity_note && pol.intensity_note.length > 0);

  const rows = [
    {lbl:'目标', curr: pol.target,      prev: pol.target_prev,      type:'target'},
    {lbl:'力度', curr: pol.intensity,   prev: pol.intensity_prev,   type:'intensity'},
    {lbl:'背景', curr: pol.background,  prev: pol.background_prev,  type:'bg'},
    {lbl:'组织', curr: pol.organization,prev: pol.organization_prev,type:'org'},
  ];

  // 表头年份
  const currYear = currentData && currentData.post && currentData.post.posted_at
    ? currentData.post.posted_at.slice(0,4) : '当年';
  const prevYear = String(parseInt(currYear) - 1) || '上年';

  const thead = `<tr>
    <th style="width:42px"></th>
    <th class="col-year">${currYear}</th>
    <th class="col-year">${hasPrev ? prevYear : '上年（未获取）'}</th>
  </tr>`;

  const tbody = rows.map(r => {
    let currCell, prevCell;
    if (r.type === 'intensity') {
      // 力度行：两列都显示 badge，下方附 intensity_note
      const [cCls, cLbl] = intMap[r.curr] || ['int int-w', r.curr||'—'];
      const [pCls, pLbl] = intMap[r.prev] || ['int int-w', r.prev||''];
      currCell = `<span class="${cCls}">${cLbl}</span>` +
        (pol.intensity_note ? `<div style="font-size:11px;color:var(--yellow);margin-top:4px">${esc(pol.intensity_note)}</div>` : '');
      prevCell = r.prev ? `<span class="${pCls}">${pLbl}</span>` +
        (pol.intensity_note_prev ? `<div style="font-size:11px;color:var(--yellow);margin-top:4px">${esc(pol.intensity_note_prev)}</div>` : '')
        : '<span style="color:var(--text2)">—</span>';
    } else {
      currCell = hilightField(r.curr, r.type);
      prevCell = hilightField(r.prev, r.type);
    }
    return `<tr>
      <td class="col-lbl">${r.lbl}</td>
      <td class="col-curr">${currCell}</td>
      <td class="col-prev">${prevCell}</td>
    </tr>`;
  }).join('');

  const body = `<div class="diff-body">
    <table class="diff-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>
  </div>`;

  const summaryRow = pol.change_summary
    ? `<div class="diff-summary">${esc(pol.change_summary)}</div>`
    : '';

  return `<div class="diff-card open">
    <div class="diff-hd" onclick="this.closest('.diff-card').classList.toggle('open')">
      <span class="diff-chev">▶</span>
      <span class="theme-name">${esc(pol.theme)}</span>
      ${hasChange
        ? `<span class="diff-count" style="color:var(--yellow)">有变化</span>`
        : `<span class="diff-count" style="color:var(--text2)">基本延续</span>`}
      ${summaryRow}
    </div>
    ${body}
  </div>`;
}

// v3 完整视图 card
function renderPolicyCardV3(pol) {
  const intMap = {
    strong:['int int-s','强'], moderate:['int int-m','中'], weak:['int int-w','宽']
  };
  const [intCls, intLbl] = intMap[pol.intensity] || ['int int-w', pol.intensity||'—'];

  const trendMap = {
    '升级':'trend trend-up','降级':'trend trend-down',
    '延续':'trend trend-cont','新增':'trend trend-new','删除':'trend trend-cont'
  };

  const measuresHtml = pol.measures.length
    ? `<div class="measure-list">
        ${pol.measures.map(m => {
          const tCls = trendMap[m.trend] || 'trend trend-cont';
          return `<div class="measure-item">
            <div style="padding-top:2px;flex-shrink:0"><span class="${tCls}">${esc(m.trend||'—')}</span></div>
            <div class="measure-body">
              <div class="measure-summary">${esc(m.summary)}</div>
              <div class="measure-text">${esc(m.measure_text)}</div>
              ${m.trend_note ? `<div class="measure-note">${esc(m.trend_note)}</div>` : ''}
              ${debugMode ? `<div class="measure-note" style="color:var(--border)">measure_id=${m.id}</div>` : ''}
            </div>
          </div>`;
        }).join('')}
      </div>`
    : '<div style="padding:8px 14px;font-size:12px;color:var(--text2);">暂无手段条目</div>';

  return `<div class="theme-card">
    <div class="theme-hd">
      <span class="theme-chev">▶</span>
      <span class="theme-name">${esc(pol.theme)}</span>
      <span class="${intCls}">${intLbl}</span>
      <span class="cnt">${pol.measures.length} 条手段</span>
      ${debugMode ? `<span style="font-size:10px;color:var(--text2)">id=${pol.id}</span>` : ''}
    </div>
    <div class="theme-body">
      ${pol.target ? `<div class="pol-field"><span class="pol-field-lbl">目标</span><span class="pol-field-val">${esc(pol.target)}</span></div>` : ''}
      ${pol.intensity_note ? `<div class="pol-field"><span class="pol-field-lbl">力度对比</span><span class="pol-field-val" style="color:var(--yellow)">${esc(pol.intensity_note)}</span></div>` : ''}
      ${pol.background ? `<div class="pol-field"><span class="pol-field-lbl">背景</span><span class="pol-field-val">${esc(pol.background)}</span></div>` : ''}
      ${pol.organization ? `<div class="pol-field"><span class="pol-field-lbl">组织</span><span class="pol-field-val">${esc(pol.organization)}</span></div>` : ''}
      ${measuresHtml}
    </div>
  </div>`;
}

function renderPolicyV3(data) {
  const p = data.post;
  const policies = data.policies || [];
  const facts = data.facts || [];
  const conclusions = data.conclusions || [];

  const deleted = facts.filter(f => f.summary && f.summary.startsWith('[删除]'));
  const regularFacts = facts.filter(f => !f.summary.startsWith('[删除]'));

  // Trend stats
  let nUp=0, nDown=0, nCont=0, nNew=0;
  policies.forEach(pol => pol.measures.forEach(m => {
    if (m.trend==='升级') nUp++;
    else if (m.trend==='降级') nDown++;
    else if (m.trend==='延续') nCont++;
    else if (m.trend==='新增') nNew++;
  }));
  const hasTrend = (nUp+nDown+nCont+nNew) > 0;

  let h = '';

  // Summary card
  h += `<div class="summary-card">
    <div class="s-meta">
      ${sfield('作者', esc(p.author_name))}
      ${p.posted_at ? sfield('日期', esc(p.posted_at.slice(0,10))) : ''}
      ${p.issuing_authority ? sfield('发文机关', esc(p.issuing_authority)) : ''}
      ${p.authority_level ? sfield('权威级别', esc(p.authority_level)) : ''}
      ${p.content_type ? sfield('类型', `<span class="bd bd-accent">${esc(p.content_type)}</span>`) : ''}
    </div>
    ${p.content_topic ? `<div style="font-size:15px;font-weight:600;margin-bottom:6px;">${esc(p.content_topic)}</div>` : ''}
    ${p.content_summary ? `<div class="s-summary">${esc(p.content_summary)}</div>` : ''}
  </div>`;

  if (debugMode) {
    h += `<div class="dbg-block">
      <div class="dbg-title">Chain 2 — 分类输出</div>
      <div class="dbg-grid">
        ${dbgField('内容类型', p.content_type)}
        ${p.content_type_secondary ? dbgField('次要类型', p.content_type_secondary) : ''}
        ${dbgField('内容主题', p.content_topic)}
        ${dbgField('作者意图', p.author_intent)}
        ${dbgField('发文机关', p.issuing_authority)}
        ${dbgField('机关级别', p.authority_level)}
      </div>
      ${p.intent_note ? `<div class="dbg-note">意图说明：${esc(p.intent_note)}</div>` : ''}
    </div>`;
  }

  if (debugMode && p.content) {
    h += `<div class="sec"><div class="sec-title">原始内容</div>
      <div class="raw-content">${esc(p.content)}</div></div>`;
  }

  // Trend stats
  if (hasTrend) {
    h += `<div class="sec"><div class="sec-title">手段趋势概览</div>
      <div class="cmp-stats">
        <div class="cmp-pill"><div class="cmp-num cmp-new">${nUp}</div><div class="cmp-lbl">升级</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-adj">${nNew}</div><div class="cmp-lbl">新增</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-con">${nCont}</div><div class="cmp-lbl">延续</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-del">${nDown}</div><div class="cmp-lbl">降级</div></div>
      </div></div>`;
  }

  // Policy cards
  if (policies.length) {
    const cardHtml = policyView === 'diff'
      ? policies.map(pol => renderPolicyDiffCard(pol)).join('')
      : policies.map(pol => renderPolicyCardV3(pol)).join('');
    h += `<div class="sec">
      <div class="sec-title" style="display:flex;align-items:center">
        政策主旨 <span class="cnt">${policies.length}</span>
        <div class="view-toggle">
          <button class="view-btn ${policyView==='diff'?'active':''}" data-v="diff" onclick="setPolicyView('diff')">变化视图</button>
          <button class="view-btn ${policyView==='full'?'active':''}" data-v="full" onclick="setPolicyView('full')">完整视图</button>
        </div>
      </div>
      ${cardHtml}</div>`;
  }

  if (regularFacts.length) {
    h += `<div class="sec"><div class="sec-title">事实陈述 <span class="cnt">${regularFacts.length}</span></div>
      <div class="eg">${regularFacts.map(f => renderFactCard(f)).join('')}</div></div>`;
  }

  if (deleted.length) {
    h += `<div class="sec">
      <div class="sec-title" style="color:var(--red)">删除政策 <span class="cnt">${deleted.length}</span></div>
      <div class="del-list">
        ${deleted.map(f => `<div class="del-item">• ${esc(f.summary.replace(/^\[删除\]\s*/,''))}</div>`).join('')}
      </div></div>`;
  }

  if (conclusions.length) {
    h += `<div class="sec"><div class="sec-title">结论 <span class="cnt">${conclusions.length}</span></div>
      <div class="eg">${conclusions.map(c => renderConcCard(c)).join('')}</div></div>`;
  }

  return h;
}

function renderPolicy(data) {
  // v3 数据（新 Policy 实体）
  if (data.policies && data.policies.length > 0) {
    return renderPolicyV3(data);
  }
  // v2 兼容（旧 PolicyTheme + PolicyItem）
  const p = data.post;
  const themes = data.themes || [];
  const facts = data.facts || [];
  const conclusions = data.conclusions || [];

  let nNew=0, nAdj=0, nCon=0;
  themes.forEach(t => t.items.forEach(it => {
    if (it.change_type==='新增') nNew++;
    else if (it.change_type==='调整') nAdj++;
    else if (it.change_type==='延续') nCon++;
  }));
  const deleted = facts.filter(f => f.summary && f.summary.startsWith('[删除]'));
  const regularFacts = facts.filter(f => !f.summary.startsWith('[删除]'));
  const hasCompare = themes.some(t => t.items.some(it => it.change_type));

  let h = '';

  // Summary
  h += `<div class="summary-card">
    <div class="s-meta">
      ${sfield('作者', esc(p.author_name))}
      ${p.posted_at ? sfield('日期', esc(p.posted_at.slice(0,10))) : ''}
      ${p.issuing_authority ? sfield('发文机关', esc(p.issuing_authority)) : ''}
      ${p.authority_level ? sfield('权威级别', esc(p.authority_level)) : ''}
      ${p.content_type ? sfield('类型', `<span class="bd bd-accent">${esc(p.content_type)}</span>`) : ''}
    </div>
    ${p.content_topic ? `<div style="font-size:15px;font-weight:600;margin-bottom:6px;">${esc(p.content_topic)}</div>` : ''}
    ${p.content_summary ? `<div class="s-summary">${esc(p.content_summary)}</div>` : ''}
  </div>`;

  // Debug: Chain 2
  if (debugMode) {
    h += `<div class="dbg-block">
      <div class="dbg-title">Chain 2 — 分类输出</div>
      <div class="dbg-grid">
        ${dbgField('内容类型', p.content_type)}
        ${p.content_type_secondary ? dbgField('次要类型', p.content_type_secondary) : ''}
        ${dbgField('内容主题', p.content_topic)}
        ${dbgField('作者意图', p.author_intent)}
        ${dbgField('发文机关', p.issuing_authority)}
        ${dbgField('机关级别', p.authority_level)}
      </div>
      ${p.intent_note ? `<div class="dbg-note">意图说明：${esc(p.intent_note)}</div>` : ''}
    </div>`;
  }

  // Debug: raw content
  if (debugMode && p.content) {
    h += `<div class="sec"><div class="sec-title">原始内容</div>
      <div class="raw-content">${esc(p.content)}</div></div>`;
  }

  // Compare stats
  if (hasCompare) {
    h += `<div class="sec"><div class="sec-title">年度变化概览</div>
      <div class="cmp-stats">
        <div class="cmp-pill"><div class="cmp-num cmp-new">${nNew}</div><div class="cmp-lbl">新增</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-adj">${nAdj}</div><div class="cmp-lbl">调整</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-con">${nCon}</div><div class="cmp-lbl">延续</div></div>
        <div class="cmp-pill"><div class="cmp-num cmp-del">${deleted.length}</div><div class="cmp-lbl">删除</div></div>
      </div></div>`;
  }

  // Themes
  if (themes.length) {
    h += `<div class="sec"><div class="sec-title">政策主旨 <span class="cnt">${themes.length}</span></div>
      ${themes.map(t => renderTheme(t)).join('')}</div>`;
  }

  // Regular facts
  if (regularFacts.length) {
    h += `<div class="sec"><div class="sec-title">事实陈述 <span class="cnt">${regularFacts.length}</span></div>
      <div class="eg">${regularFacts.map(f => renderFactCard(f)).join('')}</div></div>`;
  }

  // Deleted
  if (deleted.length) {
    h += `<div class="sec">
      <div class="sec-title" style="color:var(--red)">删除政策 <span class="cnt">${deleted.length}</span></div>
      <div class="del-list">
        ${deleted.map(f => `<div class="del-item">• ${esc(f.summary.replace(/^\[删除\]\s*/,''))}</div>`).join('')}
      </div></div>`;
  }

  // Conclusions
  if (conclusions.length) {
    h += `<div class="sec"><div class="sec-title">结论 <span class="cnt">${conclusions.length}</span></div>
      <div class="eg">${conclusions.map(c => renderConcCard(c)).join('')}</div></div>`;
  }

  return h;
}

function renderTheme(t) {
  const teeth = t.has_enforcement_teeth;
  const teethCls = teeth ? 'bd bd-green' : 'bd bd-gray';
  const teethLbl = teeth ? '✓ 有保障' : '△ 无保障';

  const tableHtml = t.items.length
    ? `<table class="pi-table">
        <thead><tr>
          <th>紧迫性</th><th>政策摘要</th><th>量化目标</th>
          <th>变化</th><th>执行状态</th>
          ${debugMode ? '<th style="color:var(--text2)">id</th>' : ''}
        </tr></thead>
        <tbody>${t.items.map(it => renderPolicyItem(it)).join('')}</tbody>
      </table>`
    : '<div style="padding:10px 14px;color:var(--text2);font-size:12px;">暂无政策条目</div>';

  return `<div class="theme-card">
    <div class="theme-hd">
      <span class="theme-chev">▶</span>
      <span class="theme-name">${esc(t.theme_name)}</span>
      <span class="${teethCls}" style="font-size:11px;">${teethLbl}</span>
      <span class="cnt">${t.items.length}</span>
      ${debugMode ? `<span style="font-size:10px;color:var(--text2);">id=${t.id}</span>` : ''}
    </div>
    <div class="theme-body">
      ${t.background ? `<div class="theme-bg"><div class="theme-bg-lbl">背景与目的</div>${esc(t.background)}</div>` : ''}
      ${debugMode && t.enforcement_note
        ? `<div class="enf-note"><span style="font-size:10px;font-weight:600;text-transform:uppercase;color:var(--text2)">组织保障：</span>${esc(t.enforcement_note)}</div>`
        : ''}
      ${tableHtml}
    </div>
  </div>`;
}

function renderPolicyItem(it) {
  const urgMap = {
    mandatory:['urg urg-m','强制'], encouraged:['urg urg-e','鼓励'],
    pilot:['urg urg-p','试点'],     gradual:['urg urg-g','渐进']
  };
  const [urgCls, urgLbl] = urgMap[it.urgency] || ['urg urg-g', it.urgency||'—'];
  const chgMap = {新增:'chg chg-n',调整:'chg chg-a',延续:'chg chg-c',删除:'chg chg-d'};
  const chgCls = chgMap[it.change_type] || '';
  const execIcon = {implemented:'✅',in_progress:'🔄',stalled:'⚠️',not_started:'⏳',unknown:'❓'}[it.execution_status]||'—';
  const execZh = {implemented:'已落地',in_progress:'推进中',stalled:'受阻',not_started:'未启动',unknown:'未知'}[it.execution_status]||'';

  return `<tr>
    <td>
      <span class="${urgCls}">${urgLbl}</span>
      ${it.is_hard_target ? '<div style="font-size:10px;color:var(--red);margin-top:3px;">[硬目标]</div>' : ''}
    </td>
    <td>
      <div>${esc(it.summary)}</div>
      ${debugMode ? `<div class="pi-text">${esc(it.policy_text)}</div>` : ''}
    </td>
    <td>
      ${it.metric_value
        ? `<span style="font-weight:600;color:var(--accent)">${esc(it.metric_value)}</span>`
        : '<span style="color:var(--text2)">—</span>'}
      ${debugMode && it.target_year ? `<div style="font-size:10px;color:var(--text2)">目标年：${esc(it.target_year)}</div>` : ''}
    </td>
    <td>
      ${it.change_type ? `<span class="${chgCls}">${it.change_type}</span>` : '<span style="color:var(--text2)">—</span>'}
      ${debugMode && it.change_note ? `<div class="pi-note">${esc(it.change_note)}</div>` : ''}
    </td>
    <td>
      <span class="exec">${execIcon} ${execZh}</span>
      ${debugMode && it.execution_note ? `<div class="exec-note">${esc(it.execution_note)}</div>` : ''}
    </td>
    ${debugMode ? `<td class="pi-id">${it.id}</td>` : ''}
  </tr>`;
}

// ══════════════════════════════════════════════════════════════════════════════
// STANDARD MODE
// ══════════════════════════════════════════════════════════════════════════════
function renderStandard(data) {
  const p = data.post;
  const facts = data.facts || [];
  const assumptions = data.assumptions || [];
  const implicits = data.implicit_conditions || [];
  const conclusions = data.conclusions || [];
  const predictions = data.predictions || [];
  const solutions = data.solutions || [];

  let h = '';

  h += `<div class="summary-card">
    <div class="s-meta">
      ${sfield('作者', esc(p.author_name))}
      ${p.posted_at ? sfield('日期', esc(p.posted_at.slice(0,10))) : ''}
      ${p.content_type ? sfield('类型', `<span class="bd bd-gray">${esc(p.content_type)}</span>`) : ''}
      ${p.author_intent ? sfield('意图', esc(p.author_intent)) : ''}
    </div>
    ${p.content_topic ? `<div style="font-size:15px;font-weight:600;margin-bottom:6px;">${esc(p.content_topic)}</div>` : ''}
    ${p.content_summary ? `<div class="s-summary">${esc(p.content_summary)}</div>` : ''}
  </div>`;

  if (debugMode) {
    h += `<div class="dbg-block">
      <div class="dbg-title">Chain 2 — 分类输出</div>
      <div class="dbg-grid">
        ${dbgField('内容类型', p.content_type)}
        ${p.content_type_secondary ? dbgField('次要类型', p.content_type_secondary) : ''}
        ${dbgField('内容主题', p.content_topic)}
        ${dbgField('作者意图', p.author_intent)}
      </div>
      ${p.intent_note ? `<div class="dbg-note">意图说明：${esc(p.intent_note)}</div>` : ''}
    </div>`;
  }

  if (debugMode && p.content) {
    h += `<div class="sec"><div class="sec-title">原始内容</div>
      <div class="raw-content">${esc(p.content)}</div></div>`;
  }

  const hasGraph = conclusions.length && data.edges && data.edges.length;
  if (hasGraph) {
    const total = facts.length + assumptions.length + implicits.length + conclusions.length + predictions.length;
    h += `<div class="sec">
      <div class="sec-title">论证图谱 <span class="cnt">${total} 节点 · ${data.edges.length} 边</span></div>
      <div id="dag-wrap"></div>
    </div>`;
  }

  if (facts.length) {
    h += `<div class="sec"><div class="sec-title">事实 <span class="cnt">${facts.length}</span></div>
      <div class="eg">${facts.map(f => renderFactCard(f)).join('')}</div></div>`;
  }
  if (assumptions.length) {
    h += `<div class="sec"><div class="sec-title">假设条件 <span class="cnt">${assumptions.length}</span></div>
      <div class="eg">${assumptions.map(a => renderAssumCard(a)).join('')}</div></div>`;
  }
  if (implicits.length) {
    h += `<div class="sec"><div class="sec-title">隐含条件 <span class="cnt">${implicits.length}</span></div>
      <div class="eg">${implicits.map(ic => renderImplicitCard(ic)).join('')}</div></div>`;
  }
  if (conclusions.length) {
    h += `<div class="sec"><div class="sec-title">结论 <span class="cnt">${conclusions.length}</span></div>
      <div class="eg">${conclusions.map(c => renderConcCard(c)).join('')}</div></div>`;
  }
  if (predictions.length) {
    h += `<div class="sec"><div class="sec-title">预测 <span class="cnt">${predictions.length}</span></div>
      <div class="eg">${predictions.map(p => renderPredCard(p)).join('')}</div></div>`;
  }
  if (solutions.length) {
    h += `<div class="sec"><div class="sec-title">解决方案 <span class="cnt">${solutions.length}</span></div>
      <div class="eg">${solutions.map(s => renderSolCard(s)).join('')}</div></div>`;
  }

  if (debugMode && data.edges && data.edges.length) {
    h += `<div class="sec"><div class="sec-title">关系边 <span class="cnt">${data.edges.length}</span></div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;">
        ${data.edges.map(e =>
          `<div class="edge-row">${e.source_type}[${e.source_id}] → ${e.target_type}[${e.target_id}]
           <span class="edge-type">${e.edge_type}</span></div>`
        ).join('')}
      </div></div>`;
  }

  return h;
}

// ── DAG ────────────────────────────────────────────────────────────────────────
function renderDAG(data) {
  const container = document.getElementById('dag-wrap');
  if (!container || typeof vis === 'undefined') return;

  const facts = data.facts || [];
  const assumptions = data.assumptions || [];
  const implicits = data.implicit_conditions || [];
  const conclusions = data.conclusions || [];
  const predictions = data.predictions || [];
  const edges_raw = data.edges || [];

  const fvC={credible:'#3fb950',vague:'#d29922',unreliable:'#f85149',unavailable:'#4a5568'};
  const avC={high_probability:'#3fb950',medium_probability:'#d29922',low_probability:'#f85149',unavailable:'#4a5568'};
  const cvC={confirmed:'#3fb950',partial:'#d29922',refuted:'#f85149',unverifiable:'#4a5568',pending:'#58a6ff'};
  const pvC={accurate:'#3fb950',directional:'#d29922',off_target:'#e3b341',wrong:'#f85149',pending:'#58a6ff'};

  const nodes=[], edges=[];

  facts.forEach(f => nodes.push({
    id:`fact-${f.id}`, label:crop(f.summary||f.claim,28),
    shape:'box', color:{background:fvC[f.fact_verdict]||'#4a5568',border:'#30363d'},
    font:{color:'#0d1117',size:11}, title:`Fact ${f.id} · ${f.fact_verdict||'未验证'}`
  }));
  assumptions.forEach(a => nodes.push({
    id:`assumption-${a.id}`, label:crop(a.summary||a.condition_text,28),
    shape:'diamond', color:{background:avC[a.assumption_verdict]||'#4a5568',border:'#30363d'},
    font:{color:'#0d1117',size:11}, title:`Assumption ${a.id} · ${a.assumption_verdict||'待评估'}`
  }));
  implicits.forEach(ic => nodes.push({
    id:`implicit_condition-${ic.id}`, label:crop(ic.summary||ic.condition_text,28),
    shape:'diamond', color:{background:'#6e40c9',border:'#30363d'},
    font:{color:'#fff',size:11}, title:`Implicit ${ic.id} · ${ic.implicit_verdict||'未评估'}`
  }));
  conclusions.forEach(c => nodes.push({
    id:`conclusion-${c.id}`,
    label:crop(c.summary||c.claim,28)+(c.is_core_conclusion?' ★':'')+(c.is_in_cycle?' ⚠':''),
    shape:'ellipse',
    color:{background:c.is_in_cycle?'#4a5568':(cvC[c.conclusion_verdict]||'#58a6ff'),
           border:c.is_core_conclusion?'#e6edf3':'#30363d'},
    font:{color:'#0d1117',size:11}, borderWidth:c.is_core_conclusion?2:1,
    title:`Conclusion ${c.id} · ${c.conclusion_verdict||'未推导'}`
  }));
  predictions.forEach(p => nodes.push({
    id:`prediction-${p.id}`, label:crop(p.summary||p.claim,28),
    shape:'box', color:{background:pvC[p.prediction_verdict]||'#58a6ff',border:'#30363d'},
    font:{color:'#0d1117',size:11}, title:`Prediction ${p.id} · ${p.prediction_verdict||'待验证'}`
  }));

  edges_raw.forEach((e, i) => {
    const dashed = e.source_type.includes('assumption') || e.source_type.includes('implicit');
    edges.push({id:i,
      from:`${e.source_type}-${e.source_id}`, to:`${e.target_type}-${e.target_id}`,
      arrows:'to', dashes:dashed, color:{color:'#4d5566'}, width:1.2, title:e.edge_type
    });
  });

  if (!nodes.length) {
    container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text2);font-size:13px;">无实体数据</div>';
    return;
  }

  new vis.Network(container,
    {nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges)},
    {layout:{hierarchical:{enabled:true,direction:'LR',sortMethod:'directed',
       levelSeparation:200,nodeSpacing:90}},
     physics:{enabled:false},
     interaction:{hover:true,tooltipDelay:150},
     nodes:{margin:{top:6,bottom:6,left:10,right:10}}}
  );
}

// ── Entity cards ───────────────────────────────────────────────────────────────
function renderFactCard(f) {
  const vb = factVb(f.fact_verdict);
  const txt = f.summary || f.claim || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(f.claim)}</div>
    ${f.verdict_evidence ? `<div class="ec-evidence">核查依据：${esc(f.verdict_evidence.slice(0,220))}</div>` : ''}
    <div class="ec-id">id=${f.id}</div>` : '';
  return `<div class="ec"><div class="ec-summary">${esc(txt)}</div>
    <div class="ec-footer">${vb}</div>${debug}</div>`;
}
function renderAssumCard(a) {
  const vb = assumVb(a.assumption_verdict);
  const txt = a.summary || a.condition_text || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(a.condition_text)}</div>
    ${a.verdict_evidence ? `<div class="ec-evidence">${esc(a.verdict_evidence.slice(0,200))}</div>` : ''}
    <div class="ec-id">id=${a.id}</div>` : '';
  return `<div class="ec" style="border-left:3px solid var(--yellow)">
    <div class="ec-summary">${esc(txt)}</div><div class="ec-footer">${vb}</div>${debug}</div>`;
}
function renderImplicitCard(ic) {
  const vb = implicitVb(ic.implicit_verdict);
  const txt = ic.summary || ic.condition_text || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(ic.condition_text)}</div>
    <div class="ec-id">id=${ic.id}</div>` : '';
  return `<div class="ec" style="border-left:3px solid var(--purple)">
    <div class="ec-summary">${esc(txt)}</div><div class="ec-footer">${vb}</div>${debug}</div>`;
}
function renderConcCard(c) {
  const vb = concVb(c.conclusion_verdict);
  const core = c.is_core_conclusion ? '<span class="bd bd-yellow" style="font-size:10px;padding:1px 6px;">★核心</span>' : '';
  const cycle = c.is_in_cycle ? '<span class="bd bd-red" style="font-size:10px;padding:1px 6px;">⚠循环</span>' : '';
  const txt = c.summary || c.claim || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(c.claim)}</div>
    <div class="ec-id">id=${c.id} · ${c.author_confidence||''}</div>` : '';
  return `<div class="ec" ${c.is_core_conclusion?'style="border-left:3px solid var(--yellow)"':''}>
    <div class="ec-summary">${esc(txt)}</div>
    <div class="ec-footer" style="gap:4px">${vb}${core}${cycle}</div>${debug}</div>`;
}
function renderPredCard(p) {
  const vb = predVb(p.prediction_verdict);
  const tv = p.temporal_validity === 'has_timeframe'
    ? `<span class="bd bd-blue" style="font-size:10px;padding:1px 6px;">${p.temporal_note||'有时间窗口'}</span>`
    : '<span class="bd bd-gray" style="font-size:10px;padding:1px 6px;">无时间窗口</span>';
  const txt = p.summary || p.claim || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(p.claim)}</div>
    <div class="ec-id">id=${p.id}</div>` : '';
  return `<div class="ec" style="border-left:3px solid var(--blue)">
    <div class="ec-summary">${esc(txt)}</div>
    <div class="ec-footer" style="gap:4px">${vb}${tv}</div>${debug}</div>`;
}
function renderSolCard(s) {
  const txt = s.summary || s.claim || '';
  const debug = debugMode ? `
    <div class="ec-claim">${esc(s.claim)}</div>
    <div class="ec-id">${s.action_type||''}${s.action_target?' · '+esc(s.action_target):''} id=${s.id}</div>` : '';
  return `<div class="ec" style="border-left:3px solid var(--green)">
    <div class="ec-summary">${esc(txt)}</div>${debug}</div>`;
}

// ── Verdict badges ─────────────────────────────────────────────────────────────
function factVb(v) {
  const m={credible:['bd-green','可信'],vague:['bd-yellow','模糊'],
    unreliable:['bd-red','不可信'],unavailable:['bd-gray','不可查']};
  const [c,l]=m[v]||['bd-gray','未验证'];
  return `<span class="bd ${c}">${l}</span>`;
}
function assumVb(v) {
  const m={high_probability:['bd-green','高概率'],medium_probability:['bd-yellow','中概率'],
    low_probability:['bd-red','低概率'],unavailable:['bd-gray','不可评']};
  const [c,l]=m[v]||['bd-gray','待评估'];
  return `<span class="bd ${c}">${l}</span>`;
}
function implicitVb(v) {
  const m={consensus:['bd-green','共识'],contested:['bd-yellow','有争议'],false:['bd-red','已证伪']};
  const [c,l]=m[v]||['bd-gray','未评估'];
  return `<span class="bd ${c}">${l}</span>`;
}
function concVb(v) {
  const m={confirmed:['bd-green','已确认'],partial:['bd-yellow','部分成立'],
    refuted:['bd-red','已否定'],unverifiable:['bd-gray','不可核实'],pending:['bd-blue','待验证']};
  const [c,l]=m[v]||['bd-gray','未推导'];
  return `<span class="bd ${c}">${l}</span>`;
}
function predVb(v) {
  const m={accurate:['bd-green','准确'],directional:['bd-yellow','方向正确'],
    off_target:['bd-orange','偏差'],wrong:['bd-red','错误'],pending:['bd-blue','待验证']};
  const [c,l]=m[v]||['bd-gray','未验证'];
  return `<span class="bd ${c}">${l}</span>`;
}

// ── Analyze ────────────────────────────────────────────────────────────────────
async function submitAnalyze(evt) {
  evt.preventDefault();
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;

  const bar = document.getElementById('progressBar');
  bar.classList.add('show');
  bar.innerHTML = '<span class="spill active"><span class="spill-dot"></span>启动中…</span>';

  const res = await fetch('/api/analyze', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url})
  });
  const {task_id, error} = await res.json();
  if (error) { bar.innerHTML = `<span class="spill err">✗ ${esc(error)}</span>`; return; }

  const steps = {};
  const es = new EventSource(`/stream/${task_id}`);

  es.addEventListener('step', e => {
    const d = JSON.parse(e.data);
    steps[d.num] = {...d, done:false};
    updateProgress(steps);
  });
  es.addEventListener('step_done', e => {
    const d = JSON.parse(e.data);
    steps[d.num] = {...(steps[d.num]||{}), ...d, done:true};
    updateProgress(steps);
  });
  es.addEventListener('done', e => {
    es.close();
    currentData = JSON.parse(e.data);
    renderPost(currentData);
    loadPosts();
    document.getElementById('urlInput').value = '';
    if (currentData.post && currentData.post.id) {
      document.querySelectorAll('.pi').forEach(el => el.classList.remove('active'));
      const pi = document.getElementById(`pi-${currentData.post.id}`);
      if (pi) pi.classList.add('active');
    }
    setTimeout(() => bar.classList.remove('show'), 2500);
  });
  es.addEventListener('error', e => {
    es.close();
    try { bar.innerHTML = `<span class="spill err">✗ ${esc(JSON.parse(e.data))}</span>`; }
    catch { bar.innerHTML = '<span class="spill err">✗ 分析失败</span>'; }
  });
}

function updateProgress(steps) {
  const bar = document.getElementById('progressBar');
  const pills = Object.values(steps).sort((a,b)=>a.num-b.num).map(s => {
    const cls = s.done ? 'done' : 'active';
    const icon = s.done ? '✓ ' : '';
    const detail = s.done && s.detail ? ` — ${s.detail}` : '';
    return `<div class="spill ${cls}"><div class="spill-dot"></div>${icon}${esc(s.label)}${esc(detail)}</div>`;
  });
  bar.innerHTML = pills.join('<span class="step-sep">›</span>');
}

// ── Utils ──────────────────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function crop(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0,n)+'…' : s;
}
function sfield(label, value) {
  if (!value) return '';
  return `<div class="s-field"><div class="s-label">${label}</div><div class="s-value">${value}</div></div>`;
}
function dbgField(label, value) {
  if (!value) return '';
  return `<div class="dbg-field"><div class="dbg-label">${esc(label)}</div><div class="dbg-value">${esc(value)}</div></div>`;
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


if __name__ == "__main__":
    uvicorn.run("anchor_ui:app", host="0.0.0.0", port=8765, reload=False)
