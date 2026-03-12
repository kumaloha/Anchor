"""
联网搜索辅助模块
================
为事实核查提供实时网页搜索能力。

使用 Serper.dev（Google Search API，返回结构化 SERP 数据）。
Key 未配置时返回 None，调用方降级为纯训练知识模式。

免费注册：https://serper.dev（2500 credits）
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

from anchor.config import settings


@dataclass
class SearchResult:
    title: str
    url: str
    content: str     # snippet 摘要
    score: float     # 相关性排序分 0-1


async def web_search(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
) -> list[SearchResult] | None:
    """执行联网搜索，返回结构化结果列表。

    Args:
        query:           搜索关键词
        max_results:     最多返回结果数（默认 5）
        include_domains: 优先抓取的域名列表（可选）

    Returns:
        搜索结果列表；Key 未配置或请求失败时返回 None。
    """
    if not settings.serper_api_key:
        logger.debug("[WebSearcher] SERPER_API_KEY 未配置，跳过联网搜索")
        return None

    try:
        # 域名过滤：用 Google site: 语法
        q = query
        if include_domains:
            domain_filter = " OR ".join(f"site:{d}" for d in include_domains)
            q = f"({q}) ({domain_filter})"

        payload = {
            "q": q,
            "num": max_results,
        }
        headers = {
            "X-API-KEY": settings.serper_api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        organic = data.get("organic", [])

        results = []
        for i, r in enumerate(organic[:max_results]):
            results.append(SearchResult(
                title=r.get("title", ""),
                url=r.get("link", ""),
                content=r.get("snippet", ""),
                score=round(1.0 - i * 0.1, 2),  # 按排名递减
            ))

        return results

    except Exception as exc:
        logger.warning(f"[WebSearcher] Serper 搜索失败: {exc}")
        return None


def format_search_results(results: list[SearchResult]) -> str:
    """将搜索结果格式化为 LLM 可读的文本块。"""
    if not results:
        return "（无搜索结果）"

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"[来源 {i}] {r.title}")
        lines.append(f"  URL: {r.url}")
        lines.append(f"  摘要: {r.content[:400]}" + ("…" if len(r.content) > 400 else ""))
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 针对事实核查的搜索查询构建
# ---------------------------------------------------------------------------


def build_fact_query(claim: str, verifiable_expression: str | None = None) -> str:
    """从 Fact 字段构建搜索查询字符串。

    优先使用 verifiable_expression（更精确），截短到 200 字内。
    """
    base = verifiable_expression or claim
    if len(base) > 200:
        base = base[:200]
    return base
