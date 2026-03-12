"""
v4 全链路端到端测试脚本
========================
三步流水线：
  1. 内容提取（URL → 六实体提取）
  2. 通用判断（档案 + 分类 + 利益冲突）
  3. 事实验证（Fact/Assumption/ImplicitCondition/Conclusion/Prediction）

用法：
  python run_v4_test.py "https://x.com/RayDalio/status/XXX"
  python run_v4_test.py "https://x.com/RayDalio/status/XXX" --chain 1  # 只跑内容提取
"""

from __future__ import annotations

import asyncio
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
    label_str = f"{pad}{label:<32}"
    if value is None:
        print(f"{label_str}{_dim('—')}")
    else:
        print(f"{label_str}{value}")


def _verdict_color(v):
    s = str(v) if v else "—"
    if any(k in s for k in ("confirmed", "credible", "accurate", "directional",
                             "consensus", "high_probability")):
        return _ok(s)
    elif any(k in s for k in ("refuted", "unreliable", "wrong", "low_probability", "false")):
        return _warn(s)
    elif any(k in s for k in ("vague", "partial", "off_target", "contested", "medium_probability")):
        return f"{C_WARN}{s}{C_RESET}"
    elif any(k in s for k in ("unverifiable", "unavailable", "pending")):
        return _dim(s)
    return s


async def run_test(url: str, only_chain: int | None = None):
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")

    from anchor.database.session import create_tables, AsyncSessionLocal
    from anchor.chains import run_extraction, run_assessment, run_verification
    from anchor.models import (
        Assumption, Conclusion, EntityRelationship,
        Fact, ImplicitCondition, Prediction, Solution,
    )

    await create_tables()

    # ══════════════════════════════════════════════════════════════════════
    # 内容提取 — 逻辑提炼
    # ══════════════════════════════════════════════════════════════════════
    if only_chain is None or only_chain == 1:
        _sep("内容提取 — 六实体提取")
        async with AsyncSessionLocal() as session:
            c1 = await run_extraction(url, session)

        raw_post_id = c1["raw_post_id"]
        author_id   = c1["author_id"]
        print(f"  作者：{c1['author_name']}")
        print(f"  URL:  {url}")
        print(f"  RawPost id={raw_post_id}")

        if c1.get("skipped"):
            print(f"  跳过原因：{c1.get('skip_reason', '内容已处理或不相关')}")
            if only_chain == 1:
                return
        else:
            facts       = c1["facts"]
            assumptions = c1["assumptions"]
            implicit_cs = c1["implicit_conditions"]
            conclusions = c1["conclusions"]
            predictions = c1["predictions"]
            solutions   = c1["solutions"]
            rels        = c1["relationships"]

            print(
                f"\n  提取结果："
                f"  事实×{len(facts)}  假设×{len(assumptions)}"
                f"  隐含×{len(implicit_cs)}  结论×{len(conclusions)}"
                f"  预测×{len(predictions)}  方案×{len(solutions)}"
                f"  边×{len(rels)}"
            )

            # 事实
            if facts:
                print(f"\n  {C_CYAN}事实（Fact）{C_RESET}")
                for i, f in enumerate(facts):
                    print(f"    [{i}] {f.claim[:70]}")
                    if f.verifiable_statement:
                        print(f"        ↳ {_dim(f.verifiable_statement[:70])}")

            # 假设条件
            if assumptions:
                print(f"\n  {C_CYAN}假设条件（Assumption）{C_RESET}")
                for i, a in enumerate(assumptions):
                    print(f"    [{i}] {a.condition_text[:70]}")

            # 隐含条件
            if implicit_cs:
                print(f"\n  {C_CYAN}隐含条件（ImplicitCondition）{C_RESET}")
                for i, ic in enumerate(implicit_cs):
                    consensus_tag = " [共识]" if ic.is_obvious_consensus else ""
                    print(f"    [{i}]{consensus_tag} {ic.condition_text[:70]}")

            # 结论
            if conclusions:
                print(f"\n  {C_CYAN}结论（Conclusion）{C_RESET}")
                for i, c in enumerate(conclusions):
                    core_tag  = " ★核心" if c.is_core_conclusion else ""
                    cycle_tag = " ⚠循环" if c.is_in_cycle else ""
                    conf_tag  = f" [{c.author_confidence}]" if c.author_confidence else ""
                    print(f"    [{i}]{core_tag}{cycle_tag}{conf_tag} {c.claim[:65]}")

            # 预测
            if predictions:
                print(f"\n  {C_CYAN}预测（Prediction）{C_RESET}")
                for i, p in enumerate(predictions):
                    time_tag = f" [{p.temporal_note}]" if p.temporal_note else " [no_timeframe]"
                    conf_tag = f" [{p.author_confidence}]" if p.author_confidence else ""
                    print(f"    [{i}]{time_tag}{conf_tag} {p.claim[:65]}")

            # 解决方案
            if solutions:
                print(f"\n  {C_CYAN}解决方案（Solution）{C_RESET}")
                for i, s in enumerate(solutions):
                    type_tag = f" [{s.action_type}]" if s.action_type else ""
                    print(f"    [{i}]{type_tag} {s.claim[:65]}")

            # 关系边
            if rels:
                print(f"\n  {C_CYAN}关系边（EntityRelationship）{C_RESET}")
                for r in rels[:10]:
                    print(f"    {r.source_type}[{r.source_id}] →{r.edge_type}→ {r.target_type}[{r.target_id}]")
                if len(rels) > 10:
                    print(f"    … 另有 {len(rels) - 10} 条边")
    else:
        # 只跑 chain 2/3，从 DB 读取数据
        async with AsyncSessionLocal() as session:
            from sqlmodel import select
            from anchor.models import RawPost
            from anchor.collect.input_handler import parse_url
            parsed = parse_url(url)
            rp = (await session.exec(
                select(RawPost).where(
                    RawPost.source == parsed.platform,
                    RawPost.external_id == parsed.platform_id,
                )
            )).first()
            assert rp, f"RawPost not found for URL={url}. Run 内容提取 first."
            raw_post_id = rp.id
            from anchor.models import Author
            author_rec = (await session.exec(
                select(Author).where(
                    Author.platform == rp.source,
                    Author.platform_id == rp.author_platform_id,
                )
            )).first()
            author_id = author_rec.id if author_rec else None

    # ══════════════════════════════════════════════════════════════════════
    # 通用判断 — 作者分析
    # ══════════════════════════════════════════════════════════════════════
    if (only_chain is None or only_chain == 2) and author_id:
        _sep("通用判断 — 作者分析")
        async with AsyncSessionLocal() as session:
            c2 = await run_assessment(author_id, session)

        _row("author", c2["author_name"])
        _row("role", c2["role"])
        _row("credibility_tier", c2["credibility_tier"])
        _row("expertise_areas", c2["expertise_areas"])
        _row("stance_label", c2["stance_label"])
        _row("audience", c2["audience"])
        _row("core_message", c2["core_message"])
        _row("author_summary", c2["author_summary"])

    # ══════════════════════════════════════════════════════════════════════
    # 事实验证
    # ══════════════════════════════════════════════════════════════════════
    if only_chain is None or only_chain == 3:
        _sep("事实验证")
        async with AsyncSessionLocal() as session:
            c3 = await run_verification(raw_post_id, session)

        _row("facts_verified",      c3["facts_verified"])
        _row("assumptions_verified", c3["assumptions_verified"])
        _row("implicit_verified",   c3["implicit_verified"])
        _row("conclusions_derived", c3["conclusions_derived"])
        _row("predictions_checked", c3["predictions_checked"])

        # 读取最新 verdict
        async with AsyncSessionLocal() as session:
            from sqlmodel import select

            facts       = list((await session.exec(select(Fact).where(Fact.raw_post_id == raw_post_id))).all())
            assumptions = list((await session.exec(select(Assumption).where(Assumption.raw_post_id == raw_post_id))).all())
            implicit_cs = list((await session.exec(select(ImplicitCondition).where(ImplicitCondition.raw_post_id == raw_post_id))).all())
            conclusions = list((await session.exec(select(Conclusion).where(Conclusion.raw_post_id == raw_post_id))).all())
            predictions = list((await session.exec(select(Prediction).where(Prediction.raw_post_id == raw_post_id))).all())

        if facts:
            print(f"\n  {C_CYAN}事实判定{C_RESET}")
            for f in facts:
                evid = (f.verdict_evidence or "")[:60]
                print(f"    Fact id={f.id}: {_verdict_color(f.fact_verdict)}")
                if evid:
                    print(f"      └ {_dim(evid)}")

        if assumptions:
            print(f"\n  {C_CYAN}假设判定{C_RESET}")
            for a in assumptions:
                print(f"    Assumption id={a.id}: {_verdict_color(a.assumption_verdict)}")

        if implicit_cs:
            print(f"\n  {C_CYAN}隐含条件判定{C_RESET}")
            for ic in implicit_cs:
                print(f"    ImplicitCondition id={ic.id}: {_verdict_color(ic.implicit_verdict)}")

        if conclusions:
            print(f"\n  {C_CYAN}结论裁定{C_RESET}")
            for c in conclusions:
                core_tag = " ★核心" if c.is_core_conclusion else ""
                print(f"    Conclusion id={c.id}{core_tag}: {_verdict_color(c.conclusion_verdict)}")

        if predictions:
            print(f"\n  {C_CYAN}预测裁定{C_RESET}")
            for p in predictions:
                time_tag = f" [{p.temporal_note}]" if p.temporal_note else " [no_timeframe]"
                print(f"    Prediction id={p.id}{time_tag}: {_verdict_color(p.prediction_verdict)}")

    _sep()
    print("  v4 测试完成")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python run_v4_test.py <URL> [--chain 1|2|3]")
        sys.exit(1)

    url = sys.argv[1]
    only_chain = None
    if "--chain" in sys.argv:
        idx = sys.argv.index("--chain")
        if idx + 1 < len(sys.argv):
            only_chain = int(sys.argv[idx + 1])

    asyncio.run(run_test(url, only_chain))
