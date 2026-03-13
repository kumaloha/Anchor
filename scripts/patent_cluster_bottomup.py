"""Bottom-up 专利技术路线分析 — 零先验知识，纯向量聚类

不依赖任何预定义关键词或分类体系。
管线：abstract embedding → 聚类 → LLM 自动命名 → 跨公司对比

适用于任何行业，只需提供公司名。

用法:
    PYTHONPATH=. .venv/bin/python scripts/patent_cluster_bottomup.py
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from anchor.llm_client import chat_completion, get_embeddings

OUTPUT_DIR = Path("/tmp/patent_bulk")

CLUSTER_NAMING_PROMPT = """\
以下是同一技术方向的多篇专利摘要。请用一个简短标签（2-5个英文单词）概括这组专利的共同技术方向，再用一句中文（≤50字）解释。

## 输出格式
```json
{
  "label": "英文标签（2-5词）",
  "description": "中文解释（≤50字）"
}
```

只输出 JSON。
"""


def cosine_similarity_matrix(embeddings: list[list[float]]) -> np.ndarray:
    mat = np.array(embeddings)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1
    mat_normed = mat / norms
    return mat_normed @ mat_normed.T


def hierarchical_cluster(
    sim_matrix: np.ndarray,
    threshold: float = 0.65,
    max_cluster_size: int = 0,
) -> list[int]:
    """Union-Find 聚类，可限制簇大小防止链式膨胀"""
    n = sim_matrix.shape[0]
    parent = list(range(n))
    size = [1] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                pairs.append((sim_matrix[i, j], i, j))
    pairs.sort(reverse=True)

    for _, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            # 簇大小限制：合并后不超过 max_cluster_size
            if max_cluster_size > 0 and size[ri] + size[rj] > max_cluster_size:
                continue
            if size[ri] < size[rj]:
                ri, rj = rj, ri
            parent[rj] = ri
            size[ri] += size[rj]

    roots = {}
    result = []
    for i in range(n):
        root = find(i)
        if root not in roots:
            roots[root] = len(roots)
        result.append(roots[root])
    return result


async def name_cluster(patents: list[dict]) -> dict:
    """LLM 给一个簇命名"""
    # 取最多 5 篇代表
    samples = patents[:5]
    text = "\n\n".join(
        f"Title: {p['title']}\nAbstract: {p['abstract'][:200]}"
        for p in samples
    )

    resp = await chat_completion(
        system=CLUSTER_NAMING_PROMPT,
        user=text,
        max_tokens=200,
    )
    if not resp:
        return {"label": "unknown", "description": "命名失败"}

    content = resp.content.strip()
    if content.startswith("```"):
        content = "\n".join(content.split("\n")[1:])
    if content.endswith("```"):
        content = content[:content.rfind("```")]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"label": "unknown", "description": content[:50]}


async def main():
    # 加载所有专利（不做任何过滤）
    all_patents = []
    for company_file in sorted(OUTPUT_DIR.glob("*_all_patents.json")):
        company = company_file.stem.replace("_all_patents", "").upper()
        patents = json.loads(company_file.read_text())
        for p in patents:
            p["company"] = company
            all_patents.append(p)

    if not all_patents:
        print("没有找到专利数据，先运行 patent_bulk_search.py")
        return

    print(f"加载 {len(all_patents)} 篇专利")
    for company in set(p["company"] for p in all_patents):
        count = sum(1 for p in all_patents if p["company"] == company)
        print(f"  {company}: {count}")

    # Step 1: Embedding（用 title + abstract）
    print(f"\n向量化 {len(all_patents)} 篇 abstract...")
    texts = [f"{p['title']}. {p['abstract'][:300]}" for p in all_patents]

    embeddings: list[list[float]] = []
    batch_size = 10
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batch_emb = await get_embeddings(batch)
        if not batch_emb:
            print(f"  批次 {start // batch_size + 1} 失败，跳过")
            # 填零向量占位
            embeddings.extend([[0.0] * 1024] * len(batch))
            continue
        embeddings.extend(batch_emb)
        if (start // batch_size + 1) % 20 == 0:
            print(f"  已完成 {start + len(batch)}/{len(all_patents)}")

    print(f"  向量化完成: {len(embeddings)} 条")

    # Step 2: 聚类
    print("\n计算相似度矩阵...")
    sim_matrix = cosine_similarity_matrix(embeddings)

    print("聚类...")
    labels = hierarchical_cluster(sim_matrix, threshold=0.70, max_cluster_size=30)
    n_clusters = max(labels) + 1
    print(f"  → {n_clusters} 个簇")

    # 按簇分组
    clusters: dict[int, list[dict]] = defaultdict(list)
    for patent, label in zip(all_patents, labels):
        patent["cluster"] = label
        clusters[label].append(patent)

    # 按大小排序
    sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))

    # Step 3: LLM 命名（只对 ≥3 篇的簇命名）
    print(f"\n为大簇命名（≥3 篇）...")
    cluster_names: dict[int, dict] = {}
    naming_tasks = []
    for cid, members in sorted_clusters:
        if len(members) >= 3:
            naming_tasks.append((cid, members))

    # 并发命名（每次 5 个）
    sem = asyncio.Semaphore(5)
    async def name_with_sem(cid, members):
        async with sem:
            result = await name_cluster(members)
            return cid, result

    results = await asyncio.gather(
        *[name_with_sem(cid, members) for cid, members in naming_tasks]
    )
    for cid, name_info in results:
        cluster_names[cid] = name_info

    # Step 4: 输出报告
    print(f"\n{'='*70}")
    print(f" Bottom-up 专利技术路线分析")
    print(f"{'='*70}")

    # 大簇报告
    print(f"\n## 技术方向（{n_clusters} 个簇，显示 ≥3 篇的）\n")
    for cid, members in sorted_clusters:
        if len(members) < 3:
            continue

        companies = defaultdict(int)
        for m in members:
            companies[m["company"]] += 1

        name_info = cluster_names.get(cid, {"label": f"cluster_{cid}", "description": ""})
        company_str = " | ".join(f"{c}: {n}" for c, n in sorted(companies.items(), key=lambda x: -x[1]))

        print(f"### [{name_info['label']}] — {len(members)} 篇 ({company_str})")
        if name_info["description"]:
            print(f"    {name_info['description']}")

        # 显示每家公司的代表专利
        for company in sorted(companies.keys()):
            sample = [m for m in members if m["company"] == company][:2]
            for s in sample:
                print(f"    [{company:6s}] {s['title'][:65]}")
        print()

    # 公司对比矩阵
    company_list = sorted(set(p["company"] for p in all_patents))
    print(f"\n## 公司间技术路线相似度\n")

    company_indices: dict[str, list[int]] = defaultdict(list)
    for idx, p in enumerate(all_patents):
        company_indices[p["company"]].append(idx)

    print(f"{'':12s}", end="")
    for c in company_list:
        print(f"{c:>12s}", end="")
    print()

    for c1 in company_list:
        print(f"{c1:12s}", end="")
        for c2 in company_list:
            if c1 == c2:
                print(f"{'—':>12s}", end="")
            else:
                sims = []
                # 采样（全量计算太慢）
                idx1 = company_indices[c1][:100]
                idx2 = company_indices[c2][:100]
                for i in idx1:
                    for j in idx2:
                        sims.append(sim_matrix[i, j])
                avg = np.mean(sims) if sims else 0
                marker = " ★" if avg > 0.75 else (" ●" if avg > 0.6 else "")
                print(f"{avg:>10.3f}{marker}", end="")
        print()

    # 独占 vs 共享
    print(f"\n## 技术路线独占/共享\n")
    for company in company_list:
        own = 0
        shared = 0
        own_names = []
        shared_names = []
        for cid, members in sorted_clusters:
            if len(members) < 3:
                continue
            companies_in = set(m["company"] for m in members)
            has_this = company in companies_in
            if not has_this:
                continue
            name = cluster_names.get(cid, {}).get("label", f"cluster_{cid}")
            count = sum(1 for m in members if m["company"] == company)
            if len(companies_in) == 1:
                own += 1
                own_names.append(f"{name}({count}篇)")
            else:
                shared += 1
                shared_names.append(f"{name}({count}篇)")

        total = own + shared
        print(f"  {company}:")
        print(f"    独有: {own}/{total} 方向 — {', '.join(own_names[:5])}")
        print(f"    共享: {shared}/{total} 方向 — {', '.join(shared_names[:5])}")
        print()

    # 保存
    result = {
        "patents": [{k: v for k, v in p.items() if k != "abstract"} for p in all_patents],
        "labels": labels,
        "cluster_names": {str(k): v for k, v in cluster_names.items()},
        "n_clusters": n_clusters,
    }
    out_file = OUTPUT_DIR / "bottomup_analysis.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"结果 → {out_file}")


if __name__ == "__main__":
    asyncio.run(main())
