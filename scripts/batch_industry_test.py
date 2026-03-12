"""
batch_industry_test.py — 批量测试产业链模式
============================================
从 industry.txt 提取 URL，逐个跑 run_url.py 流水线，
所有结果记录到 industry_test_log.md。

用法：
    .venv/bin/python batch_industry_test.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")

LOG_FILE = Path("industry_test_log.md")


def extract_urls() -> list[str]:
    with open("industry.txt", encoding="utf-8") as f:
        text = f.read()
    urls = re.findall(r"https://[^\s|)]+", text)
    return [u.rstrip("|") for u in urls if "参见" not in u and "官网" not in u]


def log(lines: list[str], msg: str):
    lines.append(msg)
    print(msg)


async def run_one(url: str, idx: int, total: int, lines: list[str]) -> str:
    """跑一个 URL，返回状态: ok/skip/error"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import (
        RawPost, CanonicalPlayer, SupplyNode, Issue, TechRoute, Metric,
        EntityRelationship, Fact, Conclusion, Prediction,
    )
    from sqlmodel import select

    log(lines, f"\n### [{idx}/{total}] {url}")
    log(lines, f"开始: {datetime.now().strftime('%H:%M:%S')}")

    rp_id = None
    try:
        # ── 采集 ──────────────────────────────────────────────────────────────
        from anchor.collect.input_handler import process_url
        async with AsyncSessionLocal() as s:
            result = await process_url(url, s)
        if not result or not result.raw_posts:
            log(lines, "⚠️ 采集失败（无内容）")
            return "skip"
        rp = result.raw_posts[0]
        rp_id = rp.id

        # 检查内容长度
        content_len = len((rp.content or "").strip())
        if content_len < 200:
            log(lines, f"⚠️ 内容过短 ({content_len} chars)")
            return "skip"

        log(lines, f"post_id={rp_id}, author={rp.author_name!r}, chars={content_len}")

        # ── 强制重置 ──────────────────────────────────────────────────────────
        async with AsyncSessionLocal() as s:
            rp2 = (await s.exec(select(RawPost).where(RawPost.id == rp_id))).first()
            rp2.is_processed = False
            rp2.assessed = False
            rp2.assessed_at = None
            s.add(rp2)
            await s.commit()

        # ── 通用判断 ──────────────────────────────────────────────────────────
        from anchor.chains.general_assessment import run_assessment
        async with AsyncSessionLocal() as s:
            pre = await run_assessment(rp_id, s)
        ct = pre.get("content_type", "")
        intent = pre.get("author_intent", "")
        if ct == "政策解读":
            content_mode = "policy"
        elif ct in ("产业链研究", "财经分析"):
            content_mode = "industry"
        else:
            content_mode = "standard"
        log(lines, f"content_type={ct!r}, mode={content_mode}, intent={intent!r}")

        # ── 内容提取 ──────────────────────────────────────────────────────────
        from anchor.extract.router import Extractor
        extractor = Extractor()
        async with AsyncSessionLocal() as s:
            rp3 = (await s.exec(select(RawPost).where(RawPost.id == rp_id))).first()
            result3 = await extractor.extract(
                rp3, s,
                content_mode=content_mode,
                author_intent=pre.get("author_intent"),
                force=True,
            )

        if result3 is None:
            log(lines, "⚠️ 提取返回 None")
            return "skip"
        if not result3.is_relevant_content:
            log(lines, f"⚠️ 内容不相关: {result3.skip_reason}")
            return "skip"

        nf = len(result3.facts or [])
        nc = len(result3.conclusions or [])
        np = len(result3.predictions or [])
        ns = len(result3.solutions or [])
        log(lines, f"v6: {nf}F {nc}C {np}P {ns}S")

        # ── 产业实体统计 ──────────────────────────────────────────────────────
        if content_mode == "industry":
            async with AsyncSessionLocal() as s:
                n_issues = len((await s.exec(select(Issue).where(Issue.raw_post_id == rp_id))).all())
                n_tr = len((await s.exec(select(TechRoute).where(TechRoute.raw_post_id == rp_id))).all())
                n_metrics = len((await s.exec(select(Metric).where(Metric.raw_post_id == rp_id))).all())
                industry_types = {"player", "supply_node", "issue", "tech_route", "metric"}
                all_edges = (await s.exec(
                    select(EntityRelationship).where(EntityRelationship.raw_post_id == rp_id)
                )).all()
                ind_edges = [e for e in all_edges if e.source_type in industry_types or e.target_type in industry_types]
                log(lines, f"industry: Issues={n_issues}, TechRoutes={n_tr}, Metrics={n_metrics}, IndustryEdges={len(ind_edges)}")

        log(lines, f"✅ 完成 {datetime.now().strftime('%H:%M:%S')}")
        return "ok"

    except Exception as e:
        log(lines, f"❌ 错误: {e}")
        log(lines, f"```\n{traceback.format_exc()[-500:]}\n```")
        return "error"


async def main():
    urls = extract_urls()
    lines: list[str] = [
        "# 产业链模式批量测试日志",
        f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"待测 URL: {len(urls)} 个",
        "",
    ]

    stats = {"ok": 0, "skip": 0, "error": 0}

    for i, url in enumerate(urls, 1):
        status = await run_one(url, i, len(urls), lines)
        stats[status] += 1

        # 每个 URL 完成后写一次日志（防崩溃丢失）
        _write_log(lines, stats, i, len(urls))

    log(lines, f"\n---\n## 汇总")
    log(lines, f"成功: {stats['ok']} | 跳过: {stats['skip']} | 错误: {stats['error']}")
    log(lines, f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 累计产业实体
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import CanonicalPlayer, SupplyNode
    from sqlmodel import select
    async with AsyncSessionLocal() as s:
        tp = len((await s.exec(select(CanonicalPlayer))).all())
        tn = len((await s.exec(select(SupplyNode))).all())
        log(lines, f"累计 Players={tp}, SupplyNodes={tn}")

    _write_log(lines, stats, len(urls), len(urls))


def _write_log(lines, stats, done, total):
    header = f"<!-- progress: {done}/{total} ok={stats['ok']} skip={stats['skip']} error={stats['error']} -->\n"
    LOG_FILE.write_text(header + "\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
