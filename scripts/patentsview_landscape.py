"""PatentsView 专利景观分析 — 浅层竞争情报

从 USPTO PatentsView API 拉取批量专利数据，回答三个战略问题：
1. 谁在主导行业标准（专利量排名）
2. 谁的技术有明显领先优势（被引次数、技术覆盖度）
3. 是否有代际差（时间序列趋势、新兴领域进入时间差）

前置条件:
    1. 到 https://patentsview.org/apis/purpose 免费注册获取 API key
    2. 在 .env 中添加 PATENTSVIEW_API_KEY=your_key

用法:
    PYTHONPATH=. .venv/bin/python scripts/patentsview_landscape.py
    PYTHONPATH=. .venv/bin/python scripts/patentsview_landscape.py --mode serper   # 用 Serper 快速验证
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# ===========================================================================
# 配置
# ===========================================================================

PATENTSVIEW_API = "https://search.patentsview.org/api/v1/patent"
SERPER_API = "https://google.serper.dev/search"

COMPANIES = [
    "NVIDIA",
    "Advanced Micro Devices",     # AMD
    "Intel",
    "Qualcomm",
    "Apple",
    "Google",                     # Alphabet
    "Microsoft",
    "Samsung",
    "Broadcom",
]

# GPU / AI 加速器相关 CPC 分类
CPC_DOMAINS = {
    "GPU_architecture": {
        "codes": ["G06F15/80", "G06T1/20", "G06T1/60"],
        "keywords": "GPU architecture parallel processing graphics",
    },
    "AI_ML": {
        "codes": ["G06N3/08", "G06N3/04", "G06N3/082", "G06N20/00"],
        "keywords": "neural network machine learning AI training inference",
    },
    "memory_interconnect": {
        "codes": ["G06F13/16", "H01L25/065", "H01L25/18"],
        "keywords": "HBM high bandwidth memory interconnect NVLink",
    },
    "advanced_packaging": {
        "codes": ["H01L23/538", "H01L25/0657"],
        "keywords": "chiplet advanced packaging CoWoS 2.5D 3D",
    },
    "floating_point": {
        "codes": ["G06F7/483", "G06F7/499"],
        "keywords": "floating point mixed precision quantization",
    },
}

YEAR_START = 2015
YEAR_END = 2025
OUTPUT_DIR = Path("/tmp/patent_landscape")


# ===========================================================================
# PatentsView API 模式（需要 API key）
# ===========================================================================

async def patentsview_search(
    client: httpx.AsyncClient,
    api_key: str,
    company: str,
    cpc_code: str,
    year: int,
) -> dict:
    """PatentsView API 查询"""
    query = {
        "q": {
            "_and": [
                {"_contains": {"assignees.assignee_organization": company}},
                {"_contains": {"cpcs.cpc_subgroup_id": cpc_code}},
                {"_gte": {"patent_date": f"{year}-01-01"}},
                {"_lte": {"patent_date": f"{year}-12-31"}},
            ]
        },
        "f": ["patent_id", "patent_date", "patent_title", "patent_num_cited_by_us_patents"],
        "o": {"per_page": 100},
    }

    try:
        resp = await client.post(
            PATENTSVIEW_API,
            json=query,
            headers={"X-Api-Key": api_key},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "count": data.get("total_patent_count", 0),
                "patents": data.get("patents", []),
            }
        return {"count": 0, "patents": [], "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"count": 0, "patents": [], "error": str(e)}


async def run_patentsview(api_key: str):
    """使用 PatentsView API 拉取完整数据"""
    sem = asyncio.Semaphore(3)
    all_data = {}

    async with httpx.AsyncClient() as client:
        for company in COMPANIES:
            print(f"[{company}] 拉取专利数据...")
            company_data = {}

            for domain_name, domain_info in CPC_DOMAINS.items():
                company_data[domain_name] = {}
                for year in range(YEAR_START, YEAR_END + 1):
                    total_count = 0
                    total_citations = 0

                    for cpc in domain_info["codes"]:
                        async with sem:
                            result = await patentsview_search(client, api_key, company, cpc, year)
                            total_count += result["count"]
                            for p in result.get("patents", []):
                                cited = p.get("patent_num_cited_by_us_patents")
                                if cited:
                                    total_citations += int(cited)

                    company_data[domain_name][str(year)] = {
                        "count": total_count,
                        "citations": total_citations,
                    }
                    if total_count > 0:
                        print(f"  {domain_name} {year}: {total_count} patents, {total_citations} citations")

            all_data[company] = company_data

    return all_data


# ===========================================================================
# Serper 快速模式（用已有的 Serper key）
# ===========================================================================

async def serper_patent_count(
    client: httpx.AsyncClient,
    serper_key: str,
    company: str,
    domain_name: str,
    keywords: str,
    sem: asyncio.Semaphore,
) -> dict:
    """用 Serper 搜索 Google Patents 估算专利数"""
    results_by_year = {}

    for year in range(YEAR_START, YEAR_END + 1):
        query = f'"{company}" {keywords} site:patents.google.com after:{year}-01-01 before:{year}-12-31'
        async with sem:
            try:
                resp = await client.post(
                    SERPER_API,
                    json={"q": query, "num": 10},
                    headers={"X-API-KEY": serper_key},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # searchInformation.totalResults 作为估算
                    total = int(data.get("searchInformation", {}).get("totalResults", 0))
                    organic = data.get("organic", [])
                    results_by_year[str(year)] = {
                        "estimated_count": total,
                        "sample_size": len(organic),
                        "sample_titles": [r.get("title", "")[:80] for r in organic[:3]],
                    }
                else:
                    results_by_year[str(year)] = {"estimated_count": 0, "error": f"HTTP {resp.status_code}"}
            except Exception as e:
                results_by_year[str(year)] = {"estimated_count": 0, "error": str(e)}

        await asyncio.sleep(0.5)  # 控制请求频率

    return results_by_year


async def run_serper(serper_key: str):
    """Serper 快速模式 — 搜索估算"""
    sem = asyncio.Semaphore(2)
    all_data = {}

    # Serper 模式只跑核心公司和核心领域（省 API 额度）
    core_companies = ["NVIDIA", "Advanced Micro Devices", "Intel", "Qualcomm"]
    core_domains = ["GPU_architecture", "AI_ML", "memory_interconnect"]

    async with httpx.AsyncClient() as client:
        for company in core_companies:
            print(f"\n[{company}] Serper 搜索...")
            company_data = {}

            for domain_name in core_domains:
                domain_info = CPC_DOMAINS[domain_name]
                print(f"  {domain_name}...")
                data = await serper_patent_count(
                    client, serper_key, company, domain_name,
                    domain_info["keywords"], sem,
                )
                company_data[domain_name] = data

            all_data[company] = company_data

    return all_data


# ===========================================================================
# 分析 & 报告
# ===========================================================================

def analyze_patentsview_data(all_data: dict) -> dict:
    """分析 PatentsView 数据"""
    analysis = {
        "question_1_standard_dominance": {},
        "question_2_tech_leadership": {},
        "question_3_generational_gap": {},
    }

    # Q1: 各领域专利总量排名
    for domain_name in CPC_DOMAINS:
        ranking = []
        for company, domains in all_data.items():
            if domain_name in domains:
                total = sum(yr.get("count", 0) for yr in domains[domain_name].values())
                citations = sum(yr.get("citations", 0) for yr in domains[domain_name].values())
                ranking.append({"company": company, "patents": total, "citations": citations})
        ranking.sort(key=lambda x: x["patents"], reverse=True)
        analysis["question_1_standard_dominance"][domain_name] = ranking

    # Q2: 被引影响力（技术领先度代理）
    for company, domains in all_data.items():
        total_patents = sum(
            yr.get("count", 0)
            for d in domains.values() for yr in d.values()
        )
        total_citations = sum(
            yr.get("citations", 0)
            for d in domains.values() for yr in d.values()
        )
        analysis["question_2_tech_leadership"][company] = {
            "total_patents": total_patents,
            "total_citations": total_citations,
            "citation_per_patent": round(total_citations / max(total_patents, 1), 1),
        }

    # Q3: 代际差 — 近3年 vs 前期增长
    for company, domains in all_data.items():
        recent = sum(
            yr.get("count", 0)
            for d in domains.values()
            for y, yr in d.items()
            if int(y) >= 2023
        )
        earlier = sum(
            yr.get("count", 0)
            for d in domains.values()
            for y, yr in d.items()
            if int(y) < 2023
        )
        # 首次出现年份（该公司最早有专利的年份）
        first_years = {}
        for domain_name, domain_data in domains.items():
            for y in sorted(domain_data.keys()):
                if domain_data[y].get("count", 0) > 0:
                    first_years[domain_name] = int(y)
                    break

        analysis["question_3_generational_gap"][company] = {
            "recent_3yr_patents": recent,
            "earlier_patents": earlier,
            "first_year_by_domain": first_years,
        }

    return analysis


def print_patentsview_report(analysis: dict):
    """打印 PatentsView 分析报告"""
    print("\n" + "=" * 70)
    print(" 专利景观分析 — GPU / AI 加速器领域")
    print("=" * 70)

    print("\n## Q1: 谁在主导行业标准（各领域专利量排名）")
    for domain, ranking in analysis["question_1_standard_dominance"].items():
        print(f"\n  ### {domain}")
        for i, item in enumerate(ranking[:5], 1):
            bar = "█" * min(item["patents"] // 10, 30)
            print(f"    {i}. {item['company']:25s} {item['patents']:>5d} patents  {item['citations']:>6d} cited  {bar}")

    print("\n## Q2: 谁的技术有明显领先优势（被引影响力）")
    leaders = sorted(
        analysis["question_2_tech_leadership"].items(),
        key=lambda x: x[1]["total_citations"],
        reverse=True,
    )
    print(f"  {'公司':25s} {'专利总数':>8s} {'被引总数':>8s} {'篇均被引':>8s}")
    print("  " + "-" * 52)
    for company, data in leaders:
        print(f"  {company:25s} {data['total_patents']:>8d} {data['total_citations']:>8d} {data['citation_per_patent']:>8.1f}")

    print("\n## Q3: 代际差分析（近3年趋势 + 进入时间）")
    for company, data in analysis["question_3_generational_gap"].items():
        r = data["recent_3yr_patents"]
        e = data["earlier_patents"]
        trend = "加速 ↑↑" if r > e * 0.5 else ("稳定 →" if r > e * 0.2 else "放缓 ↓")
        print(f"\n  {company}:")
        print(f"    近3年: {r:>4d}  |  2015-2022: {e:>5d}  |  趋势: {trend}")
        if data["first_year_by_domain"]:
            entries = ", ".join(f"{d}={y}" for d, y in sorted(data["first_year_by_domain"].items(), key=lambda x: x[1]))
            print(f"    最早进入: {entries}")


def print_serper_report(all_data: dict):
    """打印 Serper 快速分析报告"""
    print("\n" + "=" * 70)
    print(" 专利景观快速估算（Serper 模式 — 仅供参考）")
    print("=" * 70)

    for domain_name in ["GPU_architecture", "AI_ML", "memory_interconnect"]:
        print(f"\n  ### {domain_name}")
        for company, domains in all_data.items():
            if domain_name in domains:
                total = sum(
                    yr.get("estimated_count", 0)
                    for yr in domains[domain_name].values()
                )
                print(f"    {company:25s}  估算总数: {total:>6d}")
                # 显示最近年份的样本
                recent = domains[domain_name].get("2024", {})
                for title in recent.get("sample_titles", [])[:2]:
                    print(f"      → {title}")


# ===========================================================================
# 主函数
# ===========================================================================

async def main():
    parser = argparse.ArgumentParser(description="Patent landscape analysis")
    parser.add_argument("--mode", choices=["patentsview", "serper"], default="patentsview")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "patentsview":
        api_key = os.getenv("PATENTSVIEW_API_KEY")
        if not api_key:
            print("错误: 需要 PATENTSVIEW_API_KEY")
            print("1. 到 https://patentsview.org/apis/purpose 免费注册")
            print("2. 在 .env 中添加 PATENTSVIEW_API_KEY=your_key")
            print("\n或使用 --mode serper 用 Serper 快速估算")
            return

        all_data = await run_patentsview(api_key)
        output_file = OUTPUT_DIR / "patentsview_raw.json"
        output_file.write_text(json.dumps(all_data, indent=2, ensure_ascii=False))

        analysis = analyze_patentsview_data(all_data)
        analysis_file = OUTPUT_DIR / "landscape_analysis.json"
        analysis_file.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))

        print_patentsview_report(analysis)

    elif args.mode == "serper":
        serper_key = os.getenv("SERPER_API_KEY")
        if not serper_key:
            print("错误: 需要 SERPER_API_KEY")
            return

        all_data = await run_serper(serper_key)
        output_file = OUTPUT_DIR / "serper_raw.json"
        output_file.write_text(json.dumps(all_data, indent=2, ensure_ascii=False))

        print_serper_report(all_data)

    print(f"\n数据已保存到 {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
