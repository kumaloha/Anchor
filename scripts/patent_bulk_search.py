"""批量检索 NVIDIA / AMD 近两年专利 — Justia Patents + Jina Reader

管线:
  Step 1: Justia Patents 翻页抓取专利列表（按 assignee 倒序）
  Step 2: 过滤近两年 + 技术领域关键词筛选
  Step 3: Jina Reader 抓取专利全文 + LLM 提取 TechInsight
  Step 4: Embedding → 聚类 → 技术路线分析

用法:
    PYTHONPATH=. .venv/bin/python scripts/patent_bulk_search.py                         # Step 1-2: 搜索
    PYTHONPATH=. .venv/bin/python scripts/patent_bulk_search.py --extract               # Step 3-4: 提取+聚类
    PYTHONPATH=. .venv/bin/python scripts/patent_bulk_search.py --extract --max 30      # 每公司最多30篇
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import httpx

OUTPUT_DIR = Path("/tmp/patent_bulk")

# ===========================================================================
# 配置
# ===========================================================================

COMPANIES = {
    "NVIDIA": "nvidia-corporation",
    "AMD": "advanced-micro-devices-inc",
}

# 技术领域关键词 — 用于从 title/abstract 过滤 GPU/AI 相关专利
# 排除明显无关的（汽车安全、图像白平衡等消费级应用）
TECH_KEYWORDS = [
    # GPU / 并行计算
    "gpu", "graphics processing", "parallel process", "shader", "rasteriz",
    "ray tracing", "rendering pipeline", "compute unit", "warp", "thread block",
    "SIMD", "SIMT", "stream processor",
    # AI / 深度学习
    "neural network", "deep learning", "machine learning", "inference",
    "training", "transformer", "attention mechanism", "convolution",
    "tensor", "matrix multiply", "GEMM", "activation", "backpropagation",
    "quantiz", "mixed precision", "floating point", "bfloat",
    "large language model", "generative ai", "diffusion model",
    # 内存 / 互联
    "memory bandwidth", "HBM", "high bandwidth memory", "cache hierarch",
    "memory controller", "NVLink", "interconnect", "coheren", "CXL",
    "PCIe", "memory access", "memory management", "virtual memory",
    "unified memory", "memory pool",
    # 封装 / 芯片架构
    "chiplet", "multi-chip", "advanced packaging", "interposer",
    "2.5D", "3D stack", "die-to-die", "silicon bridge",
    "system on chip", "SoC", "network on chip", "NoC",
    # 编译器 / 调度
    "compiler", "instruction schedul", "kernel launch", "workload distribut",
    "task schedul", "load balanc", "resource allocat",
    # 功耗
    "power management", "voltage scaling", "clock gating", "thermal",
    "energy efficien",
    # 视频编解码 (GPU加速)
    "video encod", "video decod", "codec",
    # 数据中心
    "data center", "accelerator", "heterogeneous comput",
]

# 排除关键词 — 明显无关的专利
EXCLUDE_KEYWORDS = [
    "autonomous driv", "self-driving", "lidar", "radar sensor",
    "medical imag", "surgical", "drug discover",
    "robot", "drone",
]


def is_tech_relevant(title: str, abstract: str) -> bool:
    """判断专利是否与 GPU/AI 技术相关"""
    text = (title + " " + abstract).lower()

    # 排除
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in text:
            return False

    # 包含
    for kw in TECH_KEYWORDS:
        if kw.lower() in text:
            return True

    return False


def classify_tech_area(title: str, abstract: str) -> str:
    """根据 title/abstract 粗分技术领域"""
    text = (title + " " + abstract).lower()

    area_keywords = {
        "AI_training": ["training", "backpropagation", "gradient", "loss function", "optimizer"],
        "AI_inference": ["inference", "quantiz", "pruning", "deployment", "edge"],
        "transformer_LLM": ["transformer", "attention", "language model", "generative", "diffusion"],
        "GPU_architecture": ["gpu", "shader", "rasteriz", "ray tracing", "rendering", "warp", "stream processor", "SIMT"],
        "memory_HBM": ["hbm", "high bandwidth memory", "memory bandwidth", "cache", "memory controller", "memory access"],
        "interconnect": ["nvlink", "interconnect", "coheren", "cxl", "pcie", "die-to-die"],
        "chiplet_packaging": ["chiplet", "multi-chip", "interposer", "2.5d", "3d stack", "silicon bridge"],
        "compiler_scheduling": ["compiler", "instruction schedul", "kernel launch", "workload", "task schedul"],
        "power_thermal": ["power management", "voltage", "clock gating", "thermal", "energy efficien"],
        "tensor_compute": ["tensor", "matrix multiply", "gemm", "floating point", "mixed precision", "bfloat"],
        "video_codec": ["video encod", "video decod", "codec"],
        "neural_network": ["neural network", "convolution", "deep learning", "machine learning"],
    }

    for area, keywords in area_keywords.items():
        for kw in keywords:
            if kw in text:
                return area

    return "other"


# ===========================================================================
# Step 1: 抓取专利列表
# ===========================================================================

async def fetch_justia_page(client: httpx.AsyncClient, company_slug: str, page: int) -> str:
    """通过 Jina Reader 抓取 Justia Patents 一页"""
    url = f"https://patents.justia.com/assignee/{company_slug}?page={page}"
    jina_url = f"https://r.jina.ai/{url}"

    try:
        resp = await client.get(
            jina_url,
            headers={"Accept": "text/plain", "X-Return-Format": "text"},
            timeout=30,
            follow_redirects=True,
        )
        return resp.text if resp.status_code == 200 else ""
    except Exception as e:
        print(f"  [ERROR] Page {page}: {e}")
        return ""


def parse_justia_patents(text: str) -> list[dict]:
    """解析 Justia 页面中的专利条目"""
    patents = []

    # Grant 模式（宽松空白，兼容 Jina 不同页面格式）
    grant_pattern = re.compile(
        r"(.+?)\s*\n[\s\n]*Patent number:\s*(\d+)\s*\n[\s\n]*Abstract:\s*(.+?)\s*\n[\s\n]*Type:\s*Grant\s*\n[\s\n]*Filed:\s*(.+?)\s*\n[\s\n]*Date of Patent:\s*(.+?)\s*\n",
        re.DOTALL,
    )
    for m in grant_pattern.finditer(text):
        title = m.group(1).strip().split("\n")[-1].strip()
        patents.append({
            "patent_id": f"US{m.group(2)}",
            "title": title,
            "abstract": m.group(3).strip()[:300],
            "type": "grant",
            "filed": m.group(4).strip(),
            "date": m.group(5).strip(),
        })

    # Application 模式
    app_pattern = re.compile(
        r"(.+?)\s*\n[\s\n]*Publication number:\s*(\d+)\s*\n[\s\n]*Abstract:\s*(.+?)\s*\n[\s\n]*Type:\s*Application\s*\n[\s\n]*Filed:\s*(.+?)\s*\n[\s\n]*Publication date:\s*(.+?)\s*\n",
        re.DOTALL,
    )
    for m in app_pattern.finditer(text):
        title = m.group(1).strip().split("\n")[-1].strip()
        patents.append({
            "patent_id": f"US{m.group(2)}",
            "title": title,
            "abstract": m.group(3).strip()[:300],
            "type": "application",
            "filed": m.group(4).strip(),
            "date": m.group(5).strip(),
        })

    return patents


def parse_date(date_str: str) -> datetime | None:
    """解析 'March 10, 2026' 格式的日期"""
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y")
    except ValueError:
        return None


async def search_company_patents(
    client: httpx.AsyncClient,
    company: str,
    company_slug: str,
    cutoff_date: datetime,
    max_pages: int = 100,
) -> list[dict]:
    """翻页抓取一个公司的专利，直到日期超出范围"""
    all_patents = []
    reached_cutoff = False
    consecutive_empty = 0

    for page in range(1, max_pages + 1):
        print(f"  Page {page}...", end=" ", flush=True)
        text = await fetch_justia_page(client, company_slug, page)

        if not text or len(text) < 500:
            consecutive_empty += 1
            print(f"空页 ({consecutive_empty}/3)")
            if consecutive_empty >= 3:
                print("  连续3个空页，结束")
                break
            await asyncio.sleep(2)
            continue

        patents = parse_justia_patents(text)

        if not patents:
            consecutive_empty += 1
            print(f"解析 0 条 ({consecutive_empty}/3)")
            if consecutive_empty >= 3:
                print("  连续3次无结果，结束")
                break
            await asyncio.sleep(2)
            continue

        consecutive_empty = 0  # 重置

        page_added = 0
        for p in patents:
            p["company"] = company
            dt = parse_date(p["date"])
            if dt and dt < cutoff_date:
                reached_cutoff = True
                break
            all_patents.append(p)
            page_added += 1

        print(f"{len(patents)} 条 (累计 {len(all_patents)})")

        if reached_cutoff:
            print(f"  到达截止日期 {cutoff_date.strftime('%Y-%m-%d')}，停止")
            break

        await asyncio.sleep(1.5)

    return all_patents


async def step1_search():
    """Step 1: 搜索 + 过滤"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 近两年：2024-01-01 至今
    cutoff = datetime(2024, 1, 1)

    async with httpx.AsyncClient() as client:
        for company, slug in COMPANIES.items():
            print(f"\n{'='*60}")
            print(f" {company} — 抓取 {cutoff.strftime('%Y-%m-%d')} 至今的专利")
            print(f"{'='*60}")

            all_patents = await search_company_patents(client, company, slug, cutoff)
            print(f"\n  {company} 总计: {len(all_patents)} 篇")

            # 技术领域过滤
            tech_patents = []
            for p in all_patents:
                if is_tech_relevant(p["title"], p["abstract"]):
                    p["tech_area"] = classify_tech_area(p["title"], p["abstract"])
                    tech_patents.append(p)

            print(f"  GPU/AI 相关: {len(tech_patents)} 篇 ({len(tech_patents)/max(len(all_patents),1)*100:.0f}%)")

            # 按技术领域统计
            area_counts: dict[str, int] = {}
            for p in tech_patents:
                area = p["tech_area"]
                area_counts[area] = area_counts.get(area, 0) + 1

            print(f"\n  技术领域分布:")
            for area, count in sorted(area_counts.items(), key=lambda x: -x[1]):
                bar = "█" * min(count, 40)
                print(f"    {area:25s} {count:>4d} {bar}")

            # 保存全部
            all_file = OUTPUT_DIR / f"{company.lower()}_all_patents.json"
            all_file.write_text(json.dumps(all_patents, ensure_ascii=False, indent=2))

            # 保存技术相关
            tech_file = OUTPUT_DIR / f"{company.lower()}_tech_patents.json"
            tech_file.write_text(json.dumps(tech_patents, ensure_ascii=False, indent=2))

            print(f"\n  全部专利 → {all_file}")
            print(f"  技术相关 → {tech_file}")

    # 对比汇总
    print(f"\n{'='*60}")
    print(f" 汇总对比")
    print(f"{'='*60}")
    for company in COMPANIES:
        tech_file = OUTPUT_DIR / f"{company.lower()}_tech_patents.json"
        if tech_file.exists():
            patents = json.loads(tech_file.read_text())
            area_counts = {}
            for p in patents:
                area = p["tech_area"]
                area_counts[area] = area_counts.get(area, 0) + 1
            print(f"\n  {company}: {len(patents)} 篇技术专利")
            for area in sorted(set(a for p in patents for a in [p["tech_area"]])):
                count = area_counts.get(area, 0)
                print(f"    {area:25s} {count:>4d}")


async def step2_extract(max_per_company: int = 50):
    """Step 2: 批量提取 TechInsight + 聚类"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from patent_tech_routes import (
        PatentInsight,
        analyze_clusters,
        cosine_similarity_matrix,
        extract_tech_insight,
        fetch_patent_text,
        hierarchical_cluster,
    )
    from anchor.llm_client import get_embeddings

    all_insights: list[PatentInsight] = []
    cache_file = OUTPUT_DIR / "bulk_insights_cache.json"

    if cache_file.exists():
        print("发现缓存，加载...")
        cached = json.loads(cache_file.read_text())
        for item in cached:
            all_insights.append(PatentInsight(**item))
        print(f"加载 {len(all_insights)} 条 insights")
    else:
        sem = asyncio.Semaphore(2)

        for company in COMPANIES:
            tech_file = OUTPUT_DIR / f"{company.lower()}_tech_patents.json"
            if not tech_file.exists():
                print(f"[{company}] 专利列表不存在，先运行搜索")
                continue

            patents = json.loads(tech_file.read_text())

            # 按技术领域多样化采样
            by_area: dict[str, list] = {}
            for p in patents:
                by_area.setdefault(p["tech_area"], []).append(p)

            selected = []
            per_area = max(max_per_company // max(len(by_area), 1), 2)
            for area, ps in sorted(by_area.items()):
                selected.extend(ps[:per_area])
            selected = selected[:max_per_company]

            print(f"\n[{company}] 选取 {len(selected)}/{len(patents)} 篇（按领域均衡）")

            async with httpx.AsyncClient() as client:
                for i, patent in enumerate(selected):
                    pid = patent["patent_id"]
                    print(f"  [{i+1}/{len(selected)}] {pid} [{patent['tech_area']}] {patent['title'][:40]}...")

                    async with sem:
                        text = await fetch_patent_text(client, pid)

                    if not text:
                        print(f"    [SKIP] 抓取失败")
                        continue

                    insights = await extract_tech_insight(text, pid)

                    for ins in insights:
                        all_insights.append(PatentInsight(
                            company=company,
                            patent_id=pid,
                            area=patent["tech_area"],
                            technology_domain=ins.get("technology_domain", patent["tech_area"]),
                            problem=ins["problem"],
                            solutions=ins.get("solutions", []),
                            effects=ins.get("effects", []),
                            limitations=ins.get("limitations", []),
                        ))

                    await asyncio.sleep(0.5)

        cache_data = [i.to_dict() for i in all_insights]
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2))
        print(f"\n已缓存 {len(all_insights)} 条 insights")

    if len(all_insights) < 2:
        print("insights 不足")
        return

    # Embedding
    print(f"\n向量化 {len(all_insights)} 条...")
    texts = [i.embed_text for i in all_insights]
    embeddings: list[list[float]] = []
    batch_size = 10
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batch_emb = await get_embeddings(batch)
        if not batch_emb:
            print("Embedding 失败")
            return
        embeddings.extend(batch_emb)

    for ins, emb in zip(all_insights, embeddings):
        ins.embedding = emb

    sim_matrix = cosine_similarity_matrix(embeddings)
    labels = hierarchical_cluster(sim_matrix, threshold=0.65)
    analyze_clusters(all_insights, labels, sim_matrix)

    result = {
        "insights": [i.to_dict() for i in all_insights],
        "labels": labels,
    }
    result_file = OUTPUT_DIR / "bulk_analysis.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n结果 → {result_file}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true", help="提取+聚类（默认只搜索）")
    parser.add_argument("--max", type=int, default=50, help="每公司最多提取多少篇")
    args = parser.parse_args()

    if args.extract:
        await step2_extract(args.max)
    else:
        await step1_search()


if __name__ == "__main__":
    asyncio.run(main())
