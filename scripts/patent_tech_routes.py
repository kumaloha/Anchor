"""专利技术路线聚类分析

将多家公司的专利 problem+solutions 向量化，聚类后分析技术路线异同。

管线:
  1. Google Patents 抓取专利全文
  2. LLM 提取 TechInsight（problem → solutions → effects → limitations）
  3. problem+solutions 文本 → embedding 向量
  4. 余弦相似度矩阵 → 层次聚类 → 技术路线图

用法:
    PYTHONPATH=. .venv/bin/python scripts/patent_tech_routes.py
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np

from anchor.llm_client import chat_completion, get_embeddings

# ===========================================================================
# 专利列表 — 按公司 × 技术领域整理
# GPU / AI 加速器相关专利（从 Google Patents 手工筛选的代表性专利）
# ===========================================================================

PATENTS: dict[str, list[dict]] = {
    "NVIDIA": [
        {"id": "US11562247B2", "area": "training_compression", "title": "Neural Network Activation Compression with Non-Uniform Mantissas"},
        {"id": "US10402937B2", "area": "multi_gpu", "title": "Multi-GPU frame rendering"},
        {"id": "US11797474B2", "area": "gpu_arch", "title": "High performance processor"},
        {"id": "US12020035B2", "area": "interconnect", "title": "Programmatically controlled data multicasting"},
        {"id": "US11868250B2", "area": "tensor_core", "title": "Performing matrix operations on tensor cores"},
    ],
    "AMD": [
        {"id": "US11609792B2", "area": "chiplet", "title": "Active bridge chiplet with integrated cache"},
        {"id": "US11726553B2", "area": "gpu_arch", "title": "GPU with hierarchical cache"},
        {"id": "US11586479B2", "area": "memory", "title": "Memory controller with adaptive bandwidth allocation"},
        {"id": "US11741034B2", "area": "compute", "title": "Accelerated processing device for machine learning"},
        {"id": "US11599376B2", "area": "interconnect", "title": "Infinity fabric interconnect"},
    ],
    "Intel": [
        {"id": "US11610098B2", "area": "gpu_arch", "title": "GPU instruction set architecture for ML"},
        {"id": "US11734214B2", "area": "packaging", "title": "Embedded multi-die interconnect bridge"},
        {"id": "US11709780B2", "area": "memory", "title": "Compute express link memory expansion"},
        {"id": "US11620512B2", "area": "training", "title": "Neural network training with bfloat16"},
        {"id": "US11782757B2", "area": "compute", "title": "Flexible return and event delivery"},
    ],
    "Qualcomm": [
        {"id": "US11586883B2", "area": "npu", "title": "Neural processing unit architecture"},
        {"id": "US11687783B2", "area": "quantization", "title": "Quantization for neural network inference"},
        {"id": "US11610101B2", "area": "mobile_ai", "title": "On-device machine learning inference"},
        {"id": "US11625585B2", "area": "dsp", "title": "Digital signal processor for AI workloads"},
        {"id": "US11727266B2", "area": "power", "title": "Power efficient neural network processing"},
    ],
}

OUTPUT_DIR = Path("/tmp/patent_tech_routes")

# ===========================================================================
# Step 1: 抓取专利全文
# ===========================================================================

TECH_INSIGHT_PROMPT = """\
你是一位技术分析专家。从专利中提取"问题→方案→效果→局限"的结构化理解。

## 输出格式
```json
{
  "insights": [
    {
      "technology_domain": "技术领域标签（如 GPU_architecture / memory / interconnect / training / packaging / inference / quantization / power）",
      "problem": "问题/瓶颈描述 ≤300字",
      "solutions": ["方案1描述", "方案2描述"],
      "effects": ["效果1（含具体数字）", "效果2"],
      "limitations": ["局限1", "局限2"]
    }
  ]
}
```

## 提取规则
1. 一行一个问题，多个方案/效果/局限用 list 存
2. 多行可以共用同一个问题（不同角度的解法）
3. 效果要包含具体数字（如"存储减少2-4倍"）
4. 局限如果原文没写就返回空 list []
5. technology_domain 用英文标签，尽量用以下之一: GPU_architecture, memory, interconnect, training, inference, packaging, quantization, power, compiler, scheduling
6. 只输出 JSON
"""


async def fetch_patent_text(client: httpx.AsyncClient, patent_id: str) -> str | None:
    """从 Google Patents 抓取专利全文（HTML → 纯文本）"""
    url = f"https://patents.google.com/patent/{patent_id}/en"

    # 用 Jina Reader 抓取（比自己解析 HTML 简单）
    jina_url = f"https://r.jina.ai/{url}"

    try:
        resp = await client.get(
            jina_url,
            headers={
                "Accept": "text/plain",
                "X-Return-Format": "text",
            },
            timeout=30,
            follow_redirects=True,
        )
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
        print(f"  [WARN] {patent_id}: Jina 返回 {resp.status_code}, len={len(resp.text)}")
        return None
    except Exception as e:
        print(f"  [ERROR] {patent_id}: {e}")
        return None


# ===========================================================================
# Step 2: LLM 提取 TechInsight
# ===========================================================================

def clean_json(text: str) -> dict | None:
    """从 LLM 输出中解析 JSON"""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试找到 JSON 块
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
        return None


async def extract_tech_insight(patent_text: str, patent_id: str) -> list[dict]:
    """LLM 提取 TechInsight"""
    # 截断过长文本（留给 LLM 合理长度）
    max_chars = 30000
    if len(patent_text) > max_chars:
        patent_text = patent_text[:max_chars] + "\n...(truncated)"

    user_msg = f"请从以下专利中提取技术洞察：\n\n{patent_text}"
    resp = await chat_completion(system=TECH_INSIGHT_PROMPT, user=user_msg, max_tokens=4096)

    if not resp:
        print(f"  [{patent_id}] LLM 调用失败")
        return []

    data = clean_json(resp.content)
    if not data:
        print(f"  [{patent_id}] JSON 解析失败")
        return []

    insights = data.get("insights", [])
    print(f"  [{patent_id}] 提取 {len(insights)} 个问题 ({resp.input_tokens:,} in / {resp.output_tokens:,} out)")
    return insights


# ===========================================================================
# Step 3: Embedding + 聚类
# ===========================================================================

@dataclass
class PatentInsight:
    """一条专利技术洞察 + 元信息"""
    company: str
    patent_id: str
    area: str
    technology_domain: str
    problem: str
    solutions: list[str]
    effects: list[str]
    limitations: list[str]
    # 向量化后填充
    embedding: list[float] = field(default_factory=list)

    @property
    def embed_text(self) -> str:
        """用于 embedding 的文本：problem + solutions 拼接"""
        parts = [f"Problem: {self.problem}"]
        for s in self.solutions:
            parts.append(f"Solution: {s}")
        return " | ".join(parts)

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "patent_id": self.patent_id,
            "area": self.area,
            "technology_domain": self.technology_domain,
            "problem": self.problem,
            "solutions": self.solutions,
            "effects": self.effects,
            "limitations": self.limitations,
        }


def cosine_similarity_matrix(embeddings: list[list[float]]) -> np.ndarray:
    """计算余弦相似度矩阵"""
    mat = np.array(embeddings)
    # 归一化
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1
    mat_normed = mat / norms
    return mat_normed @ mat_normed.T


def hierarchical_cluster(sim_matrix: np.ndarray, threshold: float = 0.7) -> list[int]:
    """简单的层次聚类（不依赖 scipy，用贪心合并）"""
    n = sim_matrix.shape[0]
    labels = list(range(n))
    cluster_id = n

    # 找所有相似度 > threshold 的对，按相似度降序合并
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                pairs.append((sim_matrix[i, j], i, j))
    pairs.sort(reverse=True)

    label_map: dict[int, int] = {i: i for i in range(n)}

    def find(x: int) -> int:
        while label_map[x] != x:
            label_map[x] = label_map[label_map[x]]
            x = label_map[x]
        return x

    for _, i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            label_map[rj] = ri

    # 重新编号
    roots = {}
    result = []
    for i in range(n):
        root = find(i)
        if root not in roots:
            roots[root] = len(roots)
        result.append(roots[root])

    return result


# ===========================================================================
# Step 4: 分析 & 报告
# ===========================================================================

def analyze_clusters(insights: list[PatentInsight], labels: list[int], sim_matrix: np.ndarray):
    """分析聚类结果，输出技术路线差异"""
    print("\n" + "=" * 70)
    print(" 技术路线聚类分析")
    print("=" * 70)

    # 按 cluster 分组
    clusters: dict[int, list[PatentInsight]] = {}
    for insight, label in zip(insights, labels):
        clusters.setdefault(label, []).append(insight)

    print(f"\n共 {len(insights)} 条技术洞察 → {len(clusters)} 个技术路线簇")

    for cid in sorted(clusters.keys()):
        members = clusters[cid]
        companies = set(m.company for m in members)
        domains = set(m.technology_domain for m in members)

        print(f"\n### Cluster {cid} — {len(members)} 条 | 公司: {', '.join(sorted(companies))} | 领域: {', '.join(sorted(domains))}")

        if len(companies) > 1:
            print(f"    → 多公司共同追求的技术方向（竞争焦点）")
        else:
            print(f"    → {list(companies)[0]} 独有方向（差异化路线）")

        for m in members:
            problem_short = m.problem[:60] + "..." if len(m.problem) > 60 else m.problem
            print(f"    [{m.company:8s}] {m.patent_id:16s} {problem_short}")

    # 公司间相似度
    print("\n### 公司间技术路线相似度")
    company_list = sorted(set(i.company for i in insights))
    company_indices: dict[str, list[int]] = {}
    for idx, ins in enumerate(insights):
        company_indices.setdefault(ins.company, []).append(idx)

    print(f"\n{'':15s}", end="")
    for c in company_list:
        print(f"{c:>12s}", end="")
    print()

    for c1 in company_list:
        print(f"{c1:15s}", end="")
        for c2 in company_list:
            if c1 == c2:
                print(f"{'—':>12s}", end="")
            else:
                # 计算两个公司所有 insight 间的平均相似度
                sims = []
                for i in company_indices.get(c1, []):
                    for j in company_indices.get(c2, []):
                        sims.append(sim_matrix[i, j])
                avg_sim = np.mean(sims) if sims else 0
                marker = " ★" if avg_sim > 0.75 else (" ●" if avg_sim > 0.6 else "")
                print(f"{avg_sim:>10.3f}{marker}", end="")
        print()

    print("\n  ★ > 0.75 = 高度相似（同一技术路线）")
    print("  ● > 0.60 = 中度相似（有交叉）")

    # 独有 vs 共享技术方向
    print("\n### 技术路线总结")
    for company in company_list:
        own_clusters = set()
        shared_clusters = set()
        for label, ins in zip(labels, insights):
            if ins.company == company:
                cluster_companies = set(m.company for m in clusters[label])
                if len(cluster_companies) == 1:
                    own_clusters.add(label)
                else:
                    shared_clusters.add(label)

        total = len(own_clusters) + len(shared_clusters)
        if total > 0:
            own_pct = len(own_clusters) / total * 100
            print(f"\n  {company}:")
            print(f"    独有路线: {len(own_clusters)} ({own_pct:.0f}%) | 共享路线: {len(shared_clusters)} ({100-own_pct:.0f}%)")
            if own_clusters:
                for cid in own_clusters:
                    domains = set(m.technology_domain for m in clusters[cid])
                    problems = [m.problem[:50] for m in clusters[cid]]
                    print(f"    → 独有: [{', '.join(domains)}] {problems[0]}...")


# ===========================================================================
# 主函数
# ===========================================================================

async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_insights: list[PatentInsight] = []
    sem = asyncio.Semaphore(3)

    # Step 1 & 2: 抓取 + 提取
    cache_file = OUTPUT_DIR / "insights_cache.json"
    if cache_file.exists():
        print("发现缓存，加载已提取的 insights...")
        cached = json.loads(cache_file.read_text())
        for item in cached:
            all_insights.append(PatentInsight(**item))
        print(f"加载 {len(all_insights)} 条 insights")
    else:
        async with httpx.AsyncClient() as client:
            for company, patents in PATENTS.items():
                print(f"\n[{company}] 处理 {len(patents)} 篇专利")

                for patent in patents:
                    pid = patent["id"]
                    print(f"  抓取 {pid}...")

                    async with sem:
                        text = await fetch_patent_text(client, pid)

                    if not text:
                        print(f"  [SKIP] {pid} 抓取失败")
                        continue

                    print(f"  提取 TechInsight... ({len(text):,} chars)")
                    insights = await extract_tech_insight(text, pid)

                    for ins in insights:
                        all_insights.append(PatentInsight(
                            company=company,
                            patent_id=pid,
                            area=patent["area"],
                            technology_domain=ins.get("technology_domain", patent["area"]),
                            problem=ins["problem"],
                            solutions=ins.get("solutions", []),
                            effects=ins.get("effects", []),
                            limitations=ins.get("limitations", []),
                        ))

                    await asyncio.sleep(1)  # 控制请求频率

        # 缓存
        cache_data = [i.to_dict() for i in all_insights]
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2))
        print(f"\n已缓存 {len(all_insights)} 条 insights → {cache_file}")

    if len(all_insights) < 2:
        print("提取的 insights 不足，无法聚类")
        return

    # Step 3: Embedding（分批，每批 10 条）
    print(f"\n向量化 {len(all_insights)} 条 insights...")
    texts = [i.embed_text for i in all_insights]
    embeddings: list[list[float]] = []
    batch_size = 10
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batch_emb = await get_embeddings(batch)
        if not batch_emb:
            print(f"Embedding 批次 {start//batch_size + 1} 失败")
            return
        embeddings.extend(batch_emb)
        print(f"  批次 {start//batch_size + 1}: {len(batch)} 条 OK")

    for ins, emb in zip(all_insights, embeddings):
        ins.embedding = emb

    # 相似度矩阵
    sim_matrix = cosine_similarity_matrix(embeddings)

    # 聚类
    labels = hierarchical_cluster(sim_matrix, threshold=0.65)

    # Step 4: 分析
    analyze_clusters(all_insights, labels, sim_matrix)

    # 保存结果
    result = {
        "insights": [i.to_dict() for i in all_insights],
        "labels": labels,
        "similarity_matrix": sim_matrix.tolist(),
    }
    result_file = OUTPUT_DIR / "tech_route_analysis.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n完整结果已保存 → {result_file}")


if __name__ == "__main__":
    asyncio.run(main())
