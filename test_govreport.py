"""
政府工作报告 — Policy 模式完整测试（含双文档比对）
post_id=5: 2015年 李克强
post_id=6: 2026年 李强
"""
import asyncio
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")


async def run():
    from anchor.database.session import create_tables, AsyncSessionLocal
    from anchor.extract.extractor import Extractor
    from anchor.models import (
        RawPost, Fact, Conclusion, EntityRelationship,
        PolicyTheme, PolicyItem,
    )
    from sqlmodel import select

    await create_tables()
    extractor = Extractor()

    # ── Step 1: 分别提取两篇 ─────────────────────────────────────────────
    for pid in [5, 6]:
        print(f"\n{'='*70}")
        print(f"正在提取 post_id={pid} ...")
        print(f"{'='*70}")
        async with AsyncSessionLocal() as session:
            rp = (await session.exec(select(RawPost).where(RawPost.id == pid))).first()
            if not rp:
                print(f"  post_id={pid} not found, skipping")
                continue
            if rp.is_processed:
                print(f"  post_id={pid} already processed, skipping extraction")
                continue
            print(f"  [{rp.posted_at.year}] {rp.author_name} — content {len(rp.content)} chars")
            result = await extractor.extract(rp, session, content_mode="policy")
            if result is None:
                print(f"  Extraction returned None")
            elif not result.is_relevant_content:
                print(f"  Not relevant: {result.skip_reason}")
            else:
                print(f"  OK: {len(result.facts)} facts, {len(result.conclusions)} conclusions")

    # ── Step 2: 双文档比对（2026 vs 2015）────────────────────────────────
    print(f"\n{'='*70}")
    print("正在进行政策比对 (2026 vs 2015) ...")
    print(f"{'='*70}")
    async with AsyncSessionLocal() as session:
        comparison = await extractor.compare_policies(6, 5, session)
    if comparison:
        print(f"  比对完成: {len(comparison.annotations)} 条标注, {len(comparison.deleted_summaries)} 条删除")
    else:
        print("  （已标注或跳过）")

    # ── Step 3: 读回完整结果 ─────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        rp5 = (await session.exec(select(RawPost).where(RawPost.id == 5))).first()
        rp6 = (await session.exec(select(RawPost).where(RawPost.id == 6))).first()

        def load_post_data(pid):
            return {
                "themes": None, "items": None, "facts": None,
                "conclusions": None, "rels": None,
            }

        async def fetch(pid):
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
            return themes, items, facts, conclusions, rels

        themes5, items5, facts5, concs5, rels5 = await fetch(5)
        themes6, items6, facts6, concs6, rels6 = await fetch(6)

    # ── 输出 2015 结果 ────────────────────────────────────────────────────
    _print_post_result(rp5, themes5, items5, facts5, concs5, rels5)

    # ── 输出 2026 结果 ────────────────────────────────────────────────────
    _print_post_result(rp6, themes6, items6, facts6, concs6, rels6)

    # ── 输出比对摘要（直接从DB读，不依赖 comparison 返回值）────────────────
    _print_comparison_from_db(items6, themes6, facts6)


def _print_post_result(rp, themes, items, facts, conclusions, rels):
    yr = rp.posted_at.year if rp.posted_at else "?"
    print(f"\n{'#'*70}")
    print(f"# {yr}年政府工作报告  post_id={rp.id}  ({rp.author_name})")
    print(f"{'#'*70}")

    print(f"\n发文机关: {rp.issuing_authority!r}  级别: {rp.authority_level!r}")

    # theme → items 映射
    item_by_theme: dict[int, list] = {}
    for it in items:
        item_by_theme.setdefault(it.policy_theme_id, []).append(it)

    print(f"\n{'─'*60}")
    print(f"政策主旨 PolicyThemes ({len(themes)} 个)")
    print(f"{'─'*60}")
    for t in themes:
        teeth = "✓" if t.has_enforcement_teeth else "△"
        print(f"\n  【{t.theme_name}】{teeth}  (theme_id={t.id})")
        if t.background:
            print(f"    背景: {t.background}")
        if t.enforcement_note:
            print(f"    保障: {t.enforcement_note}")

        its = item_by_theme.get(t.id, [])
        if its:
            print(f"    ── 政策条目 ({len(its)} 条) ──")
            for it in its:
                urgency_zh = {
                    "mandatory": "强制", "encouraged": "鼓励",
                    "pilot": "试点", "gradual": "渐进"
                }.get(it.urgency, it.urgency)
                hard = "[硬约束]" if it.is_hard_target else ""
                metric = f" [{it.metric_value}]" if it.metric_value else ""
                year = f"({it.target_year})" if it.target_year else ""
                change = f"[{it.change_type}]" if it.change_type else "[未比对]"
                note = f" ← {it.change_note}" if it.change_note else ""
                print(f"      {change:8s}{urgency_zh:4s}{metric:12s}{year:6s} {hard}")
                print(f"        摘要: {it.summary}")
                print(f"        内容: {it.policy_text}{note}")

    print(f"\n{'─'*60}")
    print(f"变化标注事实 Facts ({len(facts)} 条)")
    print(f"{'─'*60}")
    for f in facts:
        print(f"  [{f.id}] {f.summary}")
        print(f"       {f.claim}")

    print(f"\n{'─'*60}")
    print(f"总体政策结论 Conclusions ({len(conclusions)} 条)")
    print(f"{'─'*60}")
    for c in conclusions:
        core = "★" if c.is_core_conclusion else " "
        print(f"  {core} [{c.id}] {c.summary}")
        print(f"       {c.claim}")

    print(f"\n{'─'*60}")
    print(f"关系边 ({len(rels)} 条)")
    print(f"{'─'*60}")
    by_type: dict[str, int] = {}
    for r in rels:
        by_type[r.edge_type] = by_type.get(r.edge_type, 0) + 1
    for et, cnt in sorted(by_type.items()):
        print(f"  {et}: {cnt} 条")

    print(f"\n叙事摘要: {rp.content_summary}")


def _print_comparison_from_db(current_items, current_themes, facts):
    """直接从DB中已写入的 change_type / change_note 打印比对结果"""
    theme_name_map = {t.id: t.theme_name for t in current_themes}

    print(f"\n{'#'*70}")
    print("# 双文档比对结果 (2026 vs 2015)")
    print(f"{'#'*70}")

    by_type: dict[str, list] = {"新增": [], "调整": [], "延续": []}
    for it in current_items:
        if it.change_type in by_type:
            by_type[it.change_type].append(it)

    for ct in ["新增", "调整", "延续"]:
        items = by_type[ct]
        if not items:
            continue
        print(f"\n  [{ct}] {len(items)} 条")
        for it in items:
            note = f"  ← {it.change_note}" if it.change_note else ""
            theme = theme_name_map.get(it.policy_theme_id, "")
            print(f"    • [{theme}] {it.summary}{note}")
            print(f"      {it.policy_text}")

    # 删除条目：Facts 中 summary 以 [删除] 开头的
    deleted_facts = [f for f in facts if f.summary and f.summary.startswith("[删除]")]
    if deleted_facts:
        print(f"\n  [删除（上年有、今年无）] {len(deleted_facts)} 条")
        for f in deleted_facts:
            label = f.summary.replace("[删除] ", "").replace("[删除]", "").strip()
            print(f"    • {label}")


asyncio.run(run())
