"""
批量提取 + 归一化测试
======================
1. 清空 DB
2. 每个领域 5 篇 URL 提取（公司/产业限定 AI 相关）
3. 执行节点归一化
4. 输出提取结果 + 归一化结果到本地文件
"""
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

URLS = {
    "政策 (policy)": [
        "https://www.federalreserve.gov/monetarypolicy/fomcminutes20251210.htm",
        "https://www.federalreserve.gov/monetarypolicy/fomcminutes20250917.htm",
        "https://www.federalreserve.gov/newsevents/pressreleases/monetary20251210a.htm",
        "https://www.ecb.europa.eu/press/pr/date/2025/html/ecb.mp251218~58b0e415a6.en.html",
        "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/2025/html/ecb.is250911~a13675b834.en.html",
    ],
    "产业 (industry) — AI": [
        "https://www.gartner.com/en/newsroom/press-releases/2025-08-05-gartner-hype-cycle-identifies-top-ai-innovations-in-2025",
        "https://www.gartner.com/en/newsroom/press-releases/2025-09-17-gartner-says-worldwide-ai-spending-will-total-1-point-5-trillion-in-2025",
        "https://www.gartner.com/en/newsroom/press-releases/2025-10-15-gartner-says-artificial-intelligence-optimized-iaas-is-poised-to-become-the-next-growth-engine-for-artificial-intelligence-infrastructure",
        "https://www.weforum.org/stories/2025/12/the-top-ai-stories-from-2025/",
        "https://www.brookings.edu/articles/counting-ai-a-blueprint-to-integrate-ai-investment-and-use-data-into-us-national-statistics/",
    ],
    "技术 (technology) — AI": [
        "https://arxiv.org/abs/2512.15567",
        "https://arxiv.org/abs/2501.09686",
        "https://arxiv.org/abs/2508.19828",
        "https://arxiv.org/abs/2510.24797",
        "https://arxiv.org/abs/2506.02153",
    ],
    "期货 (futures)": [
        "https://www.eia.gov/outlooks/steo/",
        "https://www.iea.org/reports/oil-market-report-december-2025",
        "https://www.bls.gov/news.release/archives/cpi_01132026.htm",
        "https://www.bls.gov/news.release/archives/empsit_01092026.htm",
        "https://www.bls.gov/news.release/eci.htm",
    ],
    "公司 (company) — AI": [
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000228/q3fy26pr.htm",
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000209/nvda-20250727.htm",
        "https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Fourth-Quarter-and-Fiscal-2026/default.aspx",
        "https://www.sec.gov/Archives/edgar/data/2488/000000248825000045/amdq125earningsslides.htm",
        "https://www.sec.gov/Archives/edgar/data/789019/000095017025061032/msft-ex99_1.htm",
    ],
    "专家 (expert)": [
        "https://x.com/RayDalio/status/2008191202751893770",
        "https://www.linkedin.com/pulse/2025-ray-dalio-kaf8e",
        "https://paulkrugman.substack.com/",
        "https://seekingalpha.com/article/4855552-ray-dalios-warning-were-headed-for-challenging-times-by-2029",
        "https://robinjbrooks.substack.com/",
    ],
}


async def run_single(url: str, label: str) -> bool:
    """运行单条 URL 的全链路提取，返回是否成功。"""
    from anchor.commands.run_url import _main_url
    try:
        await _main_url(url)
        return True
    except SystemExit:
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
        return False


def format_node(n) -> dict:
    return {
        "id": n.id,
        "raw_post_id": n.raw_post_id,
        "domain": n.domain,
        "node_type": n.node_type,
        "summary": n.summary,
        "claim": n.claim[:200],
        "abstract": n.abstract,
        "authority": n.authority,
        "valid_from": str(n.valid_from) if n.valid_from else None,
        "valid_until": str(n.valid_until) if n.valid_until else None,
        "canonical_node_id": n.canonical_node_id,
    }


def format_edge(e) -> dict:
    return {
        "id": e.id,
        "source_node_id": e.source_node_id,
        "target_node_id": e.target_node_id,
        "edge_type": e.edge_type,
        "note": e.note,
        "added_by_post_id": e.added_by_post_id,
        "authority": e.authority,
    }


async def dump_results(output_path: str):
    """从 DB 读取所有节点/边，输出到文件。"""
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import ExtractionNode, ExtractionEdge, RawPost
    from sqlmodel import select

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"Anchor 批量提取 + 归一化测试报告")
    lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 80)

    async with AsyncSessionLocal() as s:
        # 所有 RawPost
        posts = list((await s.exec(select(RawPost).order_by(RawPost.id))).all())
        lines.append(f"\n共处理 {len(posts)} 篇文章")

        # 所有节点
        nodes = list((await s.exec(
            select(ExtractionNode).order_by(ExtractionNode.id)
        )).all())
        lines.append(f"共提取 {len(nodes)} 个节点")

        # 所有边
        edges = list((await s.exec(
            select(ExtractionEdge).order_by(ExtractionEdge.id)
        )).all())
        lines.append(f"共提取 {len(edges)} 条边")

        # ── 按文章分组输出节点和边 ──
        lines.append("\n")
        lines.append("=" * 80)
        lines.append("第一部分：提取结果（按文章分组）")
        lines.append("=" * 80)

        # 建立 post_id → nodes/edges 的映射
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

            lines.append(f"\n{'─' * 70}")
            lines.append(f"📄 [{post.id}] {title}")
            lines.append(f"   URL: {post.url}")
            lines.append(f"   domain={post.content_domain}  nature={post.content_nature}")
            lines.append(f"   summary: {post.assessment_summary}")

            pn = post_nodes.get(post.id, [])
            pe = post_edges.get(post.id, [])
            lines.append(f"   节点数: {len(pn)}  边数: {len(pe)}")

            if pn:
                lines.append(f"\n   节点:")
                for n in pn:
                    canon = f" → canonical={n.canonical_node_id}" if n.canonical_node_id and n.canonical_node_id != n.id else ""
                    lines.append(
                        f"     [{n.id}] ({n.domain}/{n.node_type}) "
                        f"{n.summary} — {n.claim[:120]}"
                        f"{canon}"
                    )

            if pe:
                lines.append(f"\n   边:")
                for e in pe:
                    lines.append(
                        f"     [{e.id}] {e.source_node_id} --({e.edge_type})--> "
                        f"{e.target_node_id}  {e.note or ''}"
                    )

        # ── 归一化结果 ──
        lines.append("\n\n")
        lines.append("=" * 80)
        lines.append("第二部分：归一化结果")
        lines.append("=" * 80)

        # 找出被合并的节点（canonical_node_id != None 且 != 自身 id）
        merged = [n for n in nodes if n.canonical_node_id is not None and n.canonical_node_id != n.id]
        # 找出主节点（被指向的）
        canonical_ids = set(n.canonical_node_id for n in merged)
        node_by_id = {n.id: n for n in nodes}

        if merged:
            lines.append(f"\n共归一化合并 {len(merged)} 个节点，涉及 {len(canonical_ids)} 个主节点")

            # 按主节点分组
            groups: dict[int, list] = {}
            for n in merged:
                groups.setdefault(n.canonical_node_id, []).append(n)

            for canon_id, members in groups.items():
                canon_node = node_by_id.get(canon_id)
                if canon_node:
                    lines.append(f"\n  🔗 主节点 [{canon_id}] ({canon_node.domain}/{canon_node.node_type})")
                    lines.append(f"     {canon_node.summary} — {canon_node.claim[:120]}")
                else:
                    lines.append(f"\n  🔗 主节点 [{canon_id}] (未找到)")

                lines.append(f"     合并了以下节点:")
                for m in members:
                    lines.append(
                        f"       ← [{m.id}] ({m.domain}/{m.node_type}) "
                        f"{m.summary} — {m.claim[:100]}"
                    )
        else:
            lines.append(f"\n无归一化合并（所有节点均为独立节点）")

        # ── 统计摘要 ──
        lines.append("\n\n")
        lines.append("=" * 80)
        lines.append("第三部分：统计摘要")
        lines.append("=" * 80)

        # 按 domain 统计
        domain_stats: dict[str, dict] = {}
        for n in nodes:
            d = n.domain
            if d not in domain_stats:
                domain_stats[d] = {"nodes": 0, "merged": 0, "types": {}}
            domain_stats[d]["nodes"] += 1
            domain_stats[d]["types"][n.node_type] = domain_stats[d]["types"].get(n.node_type, 0) + 1
            if n.canonical_node_id and n.canonical_node_id != n.id:
                domain_stats[d]["merged"] += 1

        for d, stats in sorted(domain_stats.items()):
            lines.append(f"\n  {d}: {stats['nodes']} 节点, {stats['merged']} 被合并")
            for t, c in sorted(stats["types"].items(), key=lambda x: -x[1]):
                lines.append(f"    {t}: {c}")

        # 按 edge_type 统计
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
    from anchor.database.session import create_tables, engine
    from sqlmodel import SQLModel

    # ── Step 0: 清空 DB ──
    print("=" * 70)
    print("  Step 0: 清空数据库")
    print("=" * 70)
    db_path = Path("anchor.db")
    if db_path.exists():
        os.remove(db_path)
        print(f"  已删除 {db_path}")

    # 重新创建引擎（因为删了 db 文件）
    await create_tables()
    print("  表已创建")

    # ── Step 1: 批量提取 ──
    total = 0
    success = 0
    failed_urls = []

    for domain, urls in URLS.items():
        print(f"\n{'=' * 70}")
        print(f"  领域: {domain}")
        print(f"{'=' * 70}")

        for i, url in enumerate(urls, 1):
            total += 1
            print(f"\n--- [{domain}] {i}/{len(urls)} ---")
            print(f"URL: {url}")
            ok = await run_single(url, domain)
            if ok:
                success += 1
            else:
                failed_urls.append((domain, url))

    print(f"\n{'=' * 70}")
    print(f"  提取完成: {success}/{total} 成功")
    if failed_urls:
        print(f"  失败:")
        for d, u in failed_urls:
            print(f"    [{d}] {u}")
    print(f"{'=' * 70}")

    # ── Step 2: 归一化 ──
    print(f"\n{'=' * 70}")
    print(f"  Step 2: 执行节点归一化")
    print(f"{'=' * 70}")

    from anchor.database.session import AsyncSessionLocal
    from anchor.chains.canonicalize import canonicalize_nodes

    async with AsyncSessionLocal() as s:
        merge_count = await canonicalize_nodes(s)
        await s.commit()
    print(f"  归一化完成，合并 {merge_count} 对节点")

    # ── Step 3: 输出报告 ──
    output_path = f"extraction_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    await dump_results(output_path)


if __name__ == "__main__":
    asyncio.run(main())
