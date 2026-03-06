"""
Pipeline 完整调试脚本
=====================
逐步执行 Chain 1 / 2 / 3，将每步的详细输出写入 debug_output.txt。

用法：
    DATABASE_URL="sqlite+aiosqlite:///./anchor_v4_test.db" python debug_pipeline.py
"""

import asyncio
import os
import sys
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")

OUTPUT_FILE = "debug_output.txt"
POST_IDS = [9, 6]          # 先 2025，再 2026
CURRENT_POST_ID = 6        # 比对和 Chain3 执行追踪的主体
PRIOR_POST_ID   = 9


# ── 输出工具 ─────────────────────────────────────────────────────────────────

_out = None

def w(*args, **kwargs):
    line = " ".join(str(a) for a in args)
    print(line, **kwargs)
    if _out:
        print(line, file=_out)

def section(title):
    w()
    w("=" * 70)
    w(f"  {title}")
    w("=" * 70)

def subsection(title):
    w()
    w(f"── {title} " + "─" * max(0, 60 - len(title)))


# ── 主流程 ───────────────────────────────────────────────────────────────────

async def run():
    from anchor.database.session import AsyncSessionLocal
    from anchor.extract.extractor import Extractor
    from anchor.chains.chain2_author import classify_post
    from anchor.chains.chain3_verifier import run_chain3
    from anchor.models import (
        RawPost, PolicyTheme, PolicyItem,
        Fact, Conclusion, EntityRelationship,
    )
    from sqlmodel import select

    extractor = Extractor()

    # ─────────────────────────────────────────────────────────────────────────
    # CHAIN 1 + CHAIN 2（每篇依次）
    # ─────────────────────────────────────────────────────────────────────────
    for pid in POST_IDS:
        async with AsyncSessionLocal() as session:
            rp = (await session.exec(select(RawPost).where(RawPost.id == pid))).first()
            yr = rp.posted_at.year if rp.posted_at else "?"

            section(f"CHAIN 2 — post_id={pid}  [{yr}年]  {rp.author_name}")

            # Chain 2
            pre = await classify_post(rp, session)
            w(f"  content_type        : {pre.get('content_type')}")
            w(f"  content_type_2nd    : {pre.get('content_type_secondary')}")
            w(f"  content_topic       : {pre.get('content_topic')}")
            w(f"  author_intent       : {pre.get('author_intent')}")
            w(f"  intent_note         : {pre.get('intent_note')}")
            w(f"  issuing_authority   : {pre.get('issuing_authority')}")
            w(f"  authority_level     : {pre.get('authority_level')}")

            section(f"CHAIN 1 — post_id={pid}  [{yr}年]  提取")

            result = await extractor.extract(
                rp, session,
                content_mode="policy",
                author_intent=pre.get("author_intent"),
            )

            if result is None:
                w("  [!] 提取返回 None")
                continue
            if not result.is_relevant_content:
                w(f"  [!] 内容不相关: {result.skip_reason}")
                continue

            # 从 DB 读回完整结果
            themes = list((await session.exec(
                select(PolicyTheme).where(PolicyTheme.raw_post_id == pid)
            )).all())
            items = list((await session.exec(
                select(PolicyItem).where(PolicyItem.raw_post_id == pid)
            )).all())
            facts = list((await session.exec(
                select(Fact).where(Fact.raw_post_id == pid)
            )).all())
            conclusions = list((await session.exec(
                select(Conclusion).where(Conclusion.raw_post_id == pid)
            )).all())
            rels = list((await session.exec(
                select(EntityRelationship).where(EntityRelationship.raw_post_id == pid)
            )).all())

            item_by_theme = {}
            for it in items:
                item_by_theme.setdefault(it.policy_theme_id, []).append(it)

            w(f"  叙事摘要: {rp.content_summary}")
            w()
            w(f"  PolicyThemes: {len(themes)} 个")

            for t in themes:
                teeth = "✓" if t.has_enforcement_teeth else "△"
                subsection(f"主旨: 【{t.theme_name}】{teeth}  theme_id={t.id}")
                w(f"  背景: {t.background or '[空]'}")
                w(f"  保障: {t.enforcement_note or '[空]'}")

                its = item_by_theme.get(t.id, [])
                w(f"  政策条目 ({len(its)} 条):")
                for it in its:
                    urgency_zh = {"mandatory":"强制","encouraged":"鼓励",
                                  "pilot":"试点","gradual":"渐进"}.get(it.urgency, it.urgency)
                    hard   = "[硬约束]" if it.is_hard_target else ""
                    metric = f"[{it.metric_value}]" if it.metric_value else ""
                    year   = f"({it.target_year})" if it.target_year else ""
                    change = f"[{it.change_type}]" if it.change_type else "[未比对]"
                    exec_s = it.execution_status or "-"
                    w(f"    item_id={it.id}  {change:8s}  {urgency_zh:4s}  {metric:12s}{year:6s}  {hard}")
                    w(f"      摘要   : {it.summary}")
                    w(f"      内容   : {it.policy_text}")
                    w(f"      执行   : {exec_s}")

            subsection(f"Facts ({len(facts)} 条)")
            for f in facts:
                w(f"  [{f.id}] {f.summary}")
                w(f"       {f.claim}")
                w(f"       verdict={f.fact_verdict or '未验证'}")

            subsection(f"Conclusions ({len(conclusions)} 条)")
            for c in conclusions:
                core = "★" if c.is_core_conclusion else " "
                w(f"  {core}[{c.id}] {c.summary}")
                w(f"       {c.claim}")
                w(f"       verdict={c.conclusion_verdict or '未推导'}")

            edge_count = {}
            for r in rels:
                edge_count[r.edge_type] = edge_count.get(r.edge_type, 0) + 1
            w()
            w(f"  关系边 ({len(rels)} 条): " +
              ", ".join(f"{k}:{v}" for k, v in sorted(edge_count.items())))

    # ─────────────────────────────────────────────────────────────────────────
    # 双文档比对
    # ─────────────────────────────────────────────────────────────────────────
    section(f"COMPARE — post {CURRENT_POST_ID} vs {PRIOR_POST_ID}")

    async with AsyncSessionLocal() as session:
        comparison = await extractor.compare_policies(CURRENT_POST_ID, PRIOR_POST_ID, session)

    if comparison is None:
        w("  [幂等] 已标注，跳过")
    else:
        w(f"  annotations   : {len(comparison.annotations)} 条")
        w(f"  deleted       : {len(comparison.deleted_summaries)} 条")
        w()
        for ann in comparison.annotations:
            w(f"  policy_id={ann.policy_id}  change_type={ann.change_type}  note={ann.change_note}")
        if comparison.deleted_summaries:
            w()
            w("  [删除]:")
            for s in comparison.deleted_summaries:
                w(f"    • {s}")

    # ─────────────────────────────────────────────────────────────────────────
    # CHAIN 3 — 执行追踪（当年报告）
    # ─────────────────────────────────────────────────────────────────────────
    section(f"CHAIN 3 — post_id={CURRENT_POST_ID}  执行追踪 + 验证")

    async with AsyncSessionLocal() as session:
        r = await run_chain3(CURRENT_POST_ID, session)

    w(f"  items_tracked       : {r['items_tracked']}")
    w(f"  facts_verified      : {r['facts_verified']}")
    w(f"  assumptions_verified: {r['assumptions_verified']}")
    w(f"  implicit_verified   : {r['implicit_verified']}")
    w(f"  conclusions_derived : {r['conclusions_derived']}")
    w(f"  predictions_checked : {r['predictions_checked']}")

    # 读回详细执行结果
    async with AsyncSessionLocal() as session:
        items6  = list((await session.exec(
            select(PolicyItem).where(PolicyItem.raw_post_id == CURRENT_POST_ID)
        )).all())
        themes6 = list((await session.exec(
            select(PolicyTheme).where(PolicyTheme.raw_post_id == CURRENT_POST_ID)
        )).all())
        facts6  = list((await session.exec(
            select(Fact).where(Fact.raw_post_id == CURRENT_POST_ID)
        )).all())
        concs6  = list((await session.exec(
            select(Conclusion).where(Conclusion.raw_post_id == CURRENT_POST_ID)
        )).all())

    theme_name = {t.id: t.theme_name for t in themes6}

    status_zh = {
        "implemented": "✅已落地", "in_progress": "🔄推进中",
        "stalled": "⚠️受阻",  "not_started": "⏳未启动", "unknown": "❓未知",
    }

    subsection("PolicyItem 执行状态")
    for it in sorted(items6, key=lambda x: (x.policy_theme_id or 0, x.id)):
        st = status_zh.get(it.execution_status or "", f"[{it.execution_status}]")
        hard   = "[硬约束]" if it.is_hard_target else ""
        change = f"[{it.change_type}]" if it.change_type else "[未比对]"
        w(f"  [{theme_name.get(it.policy_theme_id,'')}] {it.summary}  {hard}  {change}")
        w(f"    执行: {st}")
        if it.execution_note:
            w(f"    说明: {it.execution_note}")

    subsection("Facts 验证结果")
    for f in facts6:
        w(f"  [{f.id}] {f.summary}")
        w(f"       verdict : {f.fact_verdict or '未验证'}")
        if f.verdict_evidence:
            w(f"       evidence: {f.verdict_evidence[:100]}")

    subsection("Conclusions 推导结果")
    for c in concs6:
        core = "★" if c.is_core_conclusion else " "
        w(f"  {core}[{c.id}] {c.summary}")
        w(f"       verdict : {c.conclusion_verdict or '未推导'}")

    # ─────────────────────────────────────────────────────────────────────────
    # 汇总比对视图
    # ─────────────────────────────────────────────────────────────────────────
    section(f"汇总：2026 vs 2025 比对 + 执行状态")

    by_type = {"新增": [], "调整": [], "延续": []}
    for it in items6:
        if it.change_type in by_type:
            by_type[it.change_type].append(it)

    for ct in ["新增", "调整", "延续"]:
        its = by_type[ct]
        if not its:
            continue
        w()
        w(f"  [{ct}] {len(its)} 条:")
        for it in its:
            st   = status_zh.get(it.execution_status or "", "❓")
            note = f"  ← {it.change_note}" if it.change_note else ""
            theme = theme_name.get(it.policy_theme_id, "")
            w(f"    {st}  [{theme}] {it.summary}{note}")
            w(f"         {it.policy_text}")

    deleted_facts = [f for f in facts6 if f.summary and f.summary.startswith("[删除]")]
    if deleted_facts:
        w()
        w(f"  [删除] {len(deleted_facts)} 条:")
        for f in deleted_facts:
            label = f.summary.replace("[删除] ", "").replace("[删除]", "").strip()
            w(f"    • {label}")


# ── 入口 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        _out = f
        f.write(f"Pipeline Debug Output — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Posts: {POST_IDS}  compare: {CURRENT_POST_ID} vs {PRIOR_POST_ID}\n")
        asyncio.run(run())

    print(f"\n✓ 详细输出已写入 {OUTPUT_FILE}")
