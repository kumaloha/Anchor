"""
anchor/monitor/index_crawler.py
─────────────────────────────────
多级目录页递归爬取器。

适用于 sources.yaml 中标注了 crawl_depth: N 的来源。
流程：
  1. 用 Jina Reader 抓取目录页，获取干净 Markdown
  2. 提取所有链接（anchor text + url）
  3. 调用 LLM 判断每个链接类型：article / directory / ignore
  4. article → 直接加入返回列表（过滤已处理 URL）
  5. directory → 递归（depth - 1）
  6. 最多递归 max_depth 层（用户配置，上限 3）
"""
from __future__ import annotations

import json
import re
import asyncio
import logging
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag

from anchor.monitor.feed_fetcher import FetchedItem

logger = logging.getLogger(__name__)

_MAX_LINKS_PER_PAGE = 60   # 每页最多送给 LLM 分类的链接数
_MAX_DEPTH_CAP = 3         # 全局上限

# URL 路径黑名单：常见非内容页面
_NON_CONTENT_PATH_RE = re.compile(
    r"/global-directory"
    r"|/directory"
    r"|/contact"
    r"|/careers"
    r"|/about-us"
    r"|/legal"
    r"|/privacy"
    r"|/terms"
    r"|/cookie"
    r"|/login"
    r"|/sign-in"
    r"|/register"
    r"|/account"
    r"|/search"
    r"|/sitemap"
    r"|/accessibility",
    re.IGNORECASE,
)


# ── 链接提取（从 Jina Markdown）─────────────────────────────────────────────

_MD_LINK_RE = re.compile(r"\[([^\]]{2,120})\]\((https?://[^\s)]{10,300})\)")


def _extract_md_links(jina_text: str, base_url: str) -> list[tuple[str, str]]:
    """从 Jina Markdown 中提取 (anchor_text, absolute_url) 列表。"""
    base_netloc = urlparse(base_url).netloc.lower().lstrip("www.")
    # 提取二级域名用于宽松匹配（cf40.org.cn 与 cf40.com 视为同域）
    base_sld = base_netloc.split(".")[0]
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for title, href in _MD_LINK_RE.findall(jina_text):
        href, _ = urldefrag(href)
        if not href:
            continue
        p = urlparse(href)
        # 同域检查：精确匹配 or 二级域名相同
        link_netloc = p.netloc.lower().lstrip("www.")
        if link_netloc != base_netloc and link_netloc.split(".")[0] != base_sld:
            continue
        # 过滤静态资源（用 path 判断，忽略 query string）
        p_path_lower = p.path.lower()
        if any(p_path_lower.endswith(ext) for ext in (
            ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".ico", ".zip", ".xml", ".webp",
        )):
            continue
        # 过滤 CDN / uploads 路径（图片资源但无扩展名）
        if "/wp-content/uploads/" in href or "/sites/default/files/styles/" in href:
            continue
        # 过滤纯导航路径
        if p.path in ("/", "") or not p.path:
            continue
        # 过滤非内容页面（办公室目录、联系页面等）
        if _NON_CONTENT_PATH_RE.search(p_path_lower):
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append((title.strip(), href))

    return results[:_MAX_LINKS_PER_PAGE]


# ── LLM 分类 ────────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
你是一个网页链接分类器。根据页面标题和链接信息，判断每个链接的类型。
返回严格 JSON，不要添加任何注释。"""

_CLASSIFY_USER_TMPL = """\
当前页面：「{page_title}」
URL：{page_url}

请对以下链接逐一分类（仅返回 JSON）：

规则：
- "article"：直接指向一篇具体的文章、研究报告、演讲、论文或博客文章
- "directory"：指向一个子目录、研究项目主页或文章列表页
- "ignore"：导航、作者页、标签、搜索、外部网站等无关链接

链接列表：
{links_block}

返回格式（每条对应输入顺序）：
{{"results": [{{"type": "article|directory|ignore"}}]}}
"""


async def _llm_classify_links(
    page_title: str,
    page_url: str,
    links: list[tuple[str, str]],  # (title, url)
) -> list[str]:
    """调用 LLM，返回与 links 等长的类型列表（article/directory/ignore）。"""
    from anchor.llm_client import chat_completion

    lines = [f"{i+1}. [{t}]({u})" for i, (t, u) in enumerate(links)]
    user_msg = _CLASSIFY_USER_TMPL.format(
        page_title=page_title,
        page_url=page_url,
        links_block="\n".join(lines),
    )

    resp = await chat_completion(system=_CLASSIFY_SYSTEM, user=user_msg, max_tokens=1024)
    if resp is None:
        logger.warning("[IndexCrawler] LLM classify failed, defaulting to ignore")
        return ["ignore"] * len(links)

    raw = resp.content or ""
    # 提取 JSON
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        logger.warning(f"[IndexCrawler] LLM returned no JSON: {raw[:200]}")
        return ["ignore"] * len(links)

    try:
        data = json.loads(m.group())
        results = data.get("results", [])
        types = [r.get("type", "ignore") for r in results]
        # 补齐或截断至 links 长度
        while len(types) < len(links):
            types.append("ignore")
        return types[:len(links)]
    except Exception as e:
        logger.warning(f"[IndexCrawler] JSON parse error: {e}")
        return ["ignore"] * len(links)


# ── Jina 抓取（复用 web.py 的函数）─────────────────────────────────────────

async def _fetch_page_text(url: str) -> str | None:
    """用 Jina Reader 抓取页面，返回 Markdown 文本。"""
    jina_url = "https://r.jina.ai/" + url
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            "-H", "Accept: text/plain",
            "--max-time", "25",
            jina_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or "403: Forbidden" in text:
            return None
        return text
    except Exception as exc:
        logger.warning(f"[IndexCrawler] Jina fetch failed for {url}: {exc}")
        return None


def _extract_page_title(jina_text: str) -> str:
    m = re.search(r"^Title:\s*(.+)$", jina_text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# ── 主入口：递归爬取 ─────────────────────────────────────────────────────────

async def crawl_index_page(
    url: str,
    display_name: str,
    max_depth: int,
    processed_urls: set[str],
) -> list[FetchedItem]:
    """
    递归爬取目录页，返回所有找到的文章 FetchedItem 列表。

    Args:
        url:            目录页 URL
        display_name:   sources.yaml 来源名（用于日志）
        max_depth:      最大递归深度（上限 _MAX_DEPTH_CAP）
        processed_urls: 已处理 URL 集合（用于去重，函数不修改它）
    """
    max_depth = min(max_depth, _MAX_DEPTH_CAP)
    collected: list[FetchedItem] = []
    await _crawl(url, display_name, max_depth, processed_urls, collected, visited=set())
    logger.info(f"[IndexCrawler] {display_name}: found {len(collected)} article URLs total")
    return collected


async def _crawl(
    url: str,
    page_label: str,
    depth: int,
    processed_urls: set[str],
    collected: list[FetchedItem],
    visited: set[str],
) -> None:
    if url in visited:
        return
    visited.add(url)

    logger.info(f"[IndexCrawler] depth={depth} crawling: {url}")

    text = await _fetch_page_text(url)
    if not text:
        return

    page_title = _extract_page_title(text) or page_label
    links = _extract_md_links(text, url)

    if not links:
        logger.debug(f"[IndexCrawler] No links found at {url}")
        return

    logger.info(f"[IndexCrawler] {len(links)} links → LLM classify")
    types = await _llm_classify_links(page_title, url, links)

    articles: list[tuple[str, str]] = []
    directories: list[tuple[str, str]] = []

    for (title, link_url), typ in zip(links, types):
        if typ == "article":
            articles.append((title, link_url))
        elif typ == "directory" and depth > 1:
            directories.append((title, link_url))

    logger.info(
        f"[IndexCrawler] classified: {len(articles)} articles, "
        f"{len(directories)} directories (depth={depth})"
    )

    # 文章：过滤已处理，加入结果
    for title, link_url in articles:
        if link_url not in processed_urls and link_url not in visited:
            collected.append(FetchedItem(url=link_url, title=title, raw_id=link_url))

    # 目录：递归
    for _, dir_url in directories:
        await _crawl(dir_url, page_label, depth - 1, processed_urls, collected, visited)
        await asyncio.sleep(1)   # 礼貌延时
