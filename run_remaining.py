"""
用 ConcurrentBatchRunner 并发跑剩余 URL + 归一化 + 输出报告
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 还没跑完的 URL（公司域剩余 + 专家域全部 + 期货域部分重跑）
REMAINING_URLS = [
    # 公司 (company) — AI（部分未跑）
    "https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Fourth-Quarter-and-Fiscal-2026/default.aspx",
    "https://www.sec.gov/Archives/edgar/data/2488/000000248825000045/amdq125earningsslides.htm",
    "https://www.sec.gov/Archives/edgar/data/789019/000095017025061032/msft-ex99_1.htm",
    # 专家 (expert)（全部未跑）
    "https://x.com/RayDalio/status/2008191202751893770",
    "https://www.linkedin.com/pulse/2025-ray-dalio-kaf8e",
    "https://paulkrugman.substack.com/",
    "https://seekingalpha.com/article/4855552-ray-dalios-warning-were-headed-for-challenging-times-by-2029",
    "https://robinjbrooks.substack.com/",
]


async def dump_report(output_path: str):
    """从 DB 读取全部节点/边 + 归一化结果，输出到文件。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import ExtractionNode, ExtractionEdge, RawPost
    from sqlmodel import select

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("Anchor 批量提取 + 归一化测试报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    async with AsyncSessionLocal() as s:
        posts = list((await s.exec(select(RawPost).order_by(RawPost.id))).all())
        nodes = list((await s.exec(select(ExtractionNode).order_by(ExtractionNode.id))).all())
        edges = list((await s.exec(select(ExtractionEdge).order_by(ExtractionEdge.id))).all())

        lines.append(f"\n共处理 {len(posts)} 篇文章")
        lines.append(f"共提取 {len(nodes)} 个节点")
        lines.append(f"共提取 {len(edges)} 条边")

        # ── 第一部分：按文章分组 ──
        lines.append("\n\n" + "=" * 80)
        lines.append("第一部分：提取结果（按文章分组）")
        lines.append("=" * 80)

        post_nodes: dict[int, list] = {}
        for n in nodes:
            post_nodes.setdefault(n.raw_post_id, []).append(n)
        post_edges: dict[int, list] = {}
        for e in edges:
            post_edges.setdefault(e.added_by_post_id, []).append(e)

        for post in posts:
            meta = {}
            try:
                meta = json.loads(post.raw_metadata or "{}")
            except Exception:
                pass
            title = meta.get("title", post.url or f"post#{post.id}")

            pn = post_nodes.get(post.id, [])
            pe = post_edges.get(post.id, [])

            lines.append(f"\n{'─' * 70}")
            lines.append(f"[{post.id}] {title[:80]}")
            lines.append(f"   URL: {post.url}")
            lines.append(f"   domain={post.content_domain}  nature={post.content_nature}  processed={post.is_processed}")
            lines.append(f"   summary: {post.assessment_summary}")
            lines.append(f"   节点: {len(pn)}  边: {len(pe)}")

            if pn:
                lines.append(f"\n   节点:")
                for n in pn:
                    canon = ""
                    if n.canonical_node_id and n.canonical_node_id != n.id:
                        canon = f"  → canonical={n.canonical_node_id}"
                    lines.append(
                        f"     [{n.id}] ({n.domain}/{n.node_type}) "
                        f"{n.summary} — {n.claim[:120]}{canon}"
                    )

            if pe:
                lines.append(f"\n   边:")
                for e in pe:
                    lines.append(
                        f"     [{e.id}] {e.source_node_id} --({e.edge_type})--> "
                        f"{e.target_node_id}  {e.note or ''}"
                    )

        # ── 第二部分：归一化结果 ──
        lines.append("\n\n" + "=" * 80)
        lines.append("第二部分：归一化结果")
        lines.append("=" * 80)

        merged = [n for n in nodes if n.canonical_node_id is not None and n.canonical_node_id != n.id]
        canonical_ids = set(n.canonical_node_id for n in merged)
        node_by_id = {n.id: n for n in nodes}

        if merged:
            lines.append(f"\n共归一化合并 {len(merged)} 个节点，涉及 {len(canonical_ids)} 个主节点")

            groups: dict[int, list] = {}
            for n in merged:
                groups.setdefault(n.canonical_node_id, []).append(n)

            for canon_id, members in groups.items():
                cn = node_by_id.get(canon_id)
                if cn:
                    lines.append(f"\n  主节点 [{canon_id}] ({cn.domain}/{cn.node_type})")
                    lines.append(f"     {cn.summary} — {cn.claim[:120]}")
                else:
                    lines.append(f"\n  主节点 [{canon_id}] (不在结果中)")

                lines.append(f"     合并了以下节点:")
                for m in members:
                    lines.append(
                        f"       <- [{m.id}] ({m.domain}/{m.node_type}) "
                        f"{m.summary} — {m.claim[:100]}"
                    )
        else:
            lines.append(f"\n无归一化合并（所有节点均独立）")

        # ── 第三部分：统计 ──
        lines.append("\n\n" + "=" * 80)
        lines.append("第三部分：统计摘要")
        lines.append("=" * 80)

        domain_stats: dict[str, dict] = {}
        for n in nodes:
            d = n.domain
            if d not in domain_stats:
                domain_stats[d] = {"nodes": 0, "merged": 0, "types": {}}
            domain_stats[d]["nodes"] += 1
            domain_stats[d]["types"][n.node_type] = domain_stats[d]["types"].get(n.node_type, 0) + 1
            if n.canonical_node_id and n.canonical_node_id != n.id:
                domain_stats[d]["merged"] += 1

        for d, st in sorted(domain_stats.items()):
            lines.append(f"\n  {d}: {st['nodes']} 节点, {st['merged']} 被合并")
            for t, c in sorted(st["types"].items(), key=lambda x: -x[1]):
                lines.append(f"    {t}: {c}")

        edge_type_stats: dict[str, int] = {}
        for e in edges:
            edge_type_stats[e.edge_type] = edge_type_stats.get(e.edge_type, 0) + 1
        if edge_type_stats:
            lines.append(f"\n  边类型统计:")
            for t, c in sorted(edge_type_stats.items(), key=lambda x: -x[1]):
                lines.append(f"    {t}: {c}")

    report = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入: {output_path}")
    return report


async def main():
    from anchor.database.session import create_tables
    await create_tables()

    # ── Step 1: 并发跑剩余 URL ──
    print("=" * 70)
    print(f"  并发提取剩余 {len(REMAINING_URLS)} 篇 (concurrency=5)")
    print("=" * 70)

    from anchor.pipeline.concurrent import ConcurrentBatchRunner
    runner = ConcurrentBatchRunner(concurrency=5, skip_notion=True)
    batch_result = await runner.run(REMAINING_URLS)

    print(f"\n并发提取完成:")
    print(f"  成功: {batch_result.success}/{batch_result.total}")
    print(f"  失败: {batch_result.failed}")
    print(f"  跳过: {batch_result.skipped}")
    print(f"  耗时: {batch_result.elapsed_seconds:.1f}s")

    for r in batch_result.results:
        status = "OK" if r.success else ("SKIP" if r.skipped else "FAIL")
        detail = r.skip_reason or r.error or f"{r.node_count}n/{r.edge_count}e"
        print(f"  [{status}] {r.url[:70]}  {detail}")

    # ── Step 2: 归一化 ──
    print(f"\n{'=' * 70}")
    print(f"  执行节点归一化")
    print(f"{'=' * 70}")

    from anchor.database.session import AsyncSessionLocal
    from anchor.chains.canonicalize import canonicalize_nodes

    async with AsyncSessionLocal() as s:
        merge_count = await canonicalize_nodes(s)
        await s.commit()
    print(f"  归一化完成，合并 {merge_count} 对节点")

    # ── Step 3: 输出报告 ──
    output_path = f"extraction_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    await dump_report(output_path)


if __name__ == "__main__":
    asyncio.run(main())
