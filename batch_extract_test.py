"""
批量提取测试 — 每个领域 5 篇，2025年6月后发表
产业/公司/技术限定 AI 相关内容
"""
import asyncio
import sys
import traceback

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


async def main():
    from anchor.database.session import create_tables
    await create_tables()

    total = 0
    success = 0
    failed_urls = []

    for domain, urls in URLS.items():
        print(f"\n{'='*70}")
        print(f"  领域: {domain}")
        print(f"{'='*70}")

        for i, url in enumerate(urls, 1):
            total += 1
            print(f"\n--- [{domain}] {i}/{len(urls)} ---")
            print(f"URL: {url}")
            ok = await run_single(url, domain)
            if ok:
                success += 1
            else:
                failed_urls.append((domain, url))

    print(f"\n{'='*70}")
    print(f"  完成: {success}/{total} 成功")
    if failed_urls:
        print(f"  失败:")
        for d, u in failed_urls:
            print(f"    [{d}] {u}")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
