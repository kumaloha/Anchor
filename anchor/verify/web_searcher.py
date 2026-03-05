"""
联网搜索辅助模块
================
为 Layer3 事实核查提供实时网页搜索能力。

使用 Tavily Search API（专为 LLM 应用设计，返回结构化内容摘要）。
Tavily Key 未配置时返回 None，调用方降级为纯训练知识模式。

免费注册：https://app.tavily.com（1000 次/月）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from anchor.config import settings


@dataclass
class SearchResult:
    title: str
    url: str
    content: str     # Tavily 提取的正文摘要
    score: float     # 相关性评分 0-1


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
        搜索结果列表；Tavily Key 未配置或请求失败时返回 None。
    """
    if not settings.tavily_api_key:
        logger.debug("[WebSearcher] TAVILY_API_KEY 未配置，跳过联网搜索")
        return None

    try:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=settings.tavily_api_key)
        kwargs: dict = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",   # 深度搜索，内容更丰富
            "include_answer": False,
            "include_raw_content": False,
        }
        if include_domains:
            kwargs["include_domains"] = include_domains

        resp = await client.search(**kwargs)
        results = resp.get("results", [])

        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                score=r.get("score", 0.0),
            )
            for r in results
        ]
    except Exception as exc:
        logger.warning(f"[WebSearcher] 搜索失败: {exc}")
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


def build_fact_query(claim: str, verifiable_expression: str | None) -> str:
    """从 Fact 字段构建搜索查询字符串。

    优先使用 verifiable_expression（更精确），截短到 200 字内。
    """
    base = verifiable_expression or claim
    # 截短：搜索引擎通常最佳查询长度 10-20 词
    if len(base) > 200:
        base = base[:200]
    return base
