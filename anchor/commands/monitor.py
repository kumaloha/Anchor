"""
anchor.commands.monitor — 订阅监控主流程
=========================================
从根目录 run_monitor.py 迁入，移除 sys.path hack。
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

# 只抓取此日期之后发布的内容（UTC）
DEFAULT_SINCE = datetime(2026, 3, 1, tzinfo=timezone.utc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor")


def _find_sources() -> Path:
    """查找 sources.yaml：优先 cwd，其次包根目录。"""
    cwd = Path.cwd() / "sources.yaml"
    if cwd.exists():
        return cwd
    pkg = Path(__file__).resolve().parent.parent.parent / "sources.yaml"
    if pkg.exists():
        return pkg
    return cwd  # fallback, 让后续 open 报错


# ── 付费墙检测 ────────────────────────────────────────────────────────────────

_PAYWALL_RE = re.compile(
    r"subscribe\s+to\s+(?:\w+\s+to\s+)?(read|continue|access|unlock|the full)"
    r"|subscriber[s']?\s+(only|to\s+(read|access|continue))"
    r"|members?\s+only"
    r"|sign\s+in\s+to\s+(read|access|continue|view)"
    r"|log\s+in\s+to\s+(read|access|continue|view)"
    r"|this\s+(content|article|story)\s+is\s+(only\s+)?for\s+(subscribers?|members?|premium)"
    r"|you.ve\s+(reached|used)\s+\d+\s+(of\s+(your\s+)?\d+\s+)?(free\s+)?(article|story)"
    r"|you\s+have\s+\d+\s+(free\s+)?(article|story)"
    r"|register\s+to\s+(read|access|continue)"
    r"|本文[为是]付费(内容|文章|阅读)"
    r"|订阅后[查看阅读]全文"
    r"|会员专属(内容|文章|阅读)"
    r"|付费(阅读|查看)全文"
    r"|メンバーシップ\s*¥\d+"
    r"|ここから先は.*\d+字",
    re.IGNORECASE,
)


def _is_paywalled(content: str) -> bool:
    return bool(_PAYWALL_RE.search(content))


# ── 非文章内容检测 ────────────────────────────────────────────────────────────

_NAV_LINK_RE = re.compile(r"\[[^\]]{2,}\]\(https?://[^\s)]+\)")
_ERROR_PAGE_RE = re.compile(
    r"SecurityCompromiseError"
    r"|access.+blocked"
    r"|DDoS attack suspected"
    r"|403\s+Forbidden"
    r"|Access Denied",
    re.IGNORECASE,
)

_DISCLAIMER_RE = re.compile(
    r"^Disclaimer\s*(&|and)\s*(Agreement|Notice)"
    r"|investment\s+advisory\s+or\s+similar\s+services"
    r"|not\s+available\s+to\s+(provide|most)"
    r"|^fraud\s+(and|&)\s+phishing",
    re.IGNORECASE | re.MULTILINE,
)

_INVEST_DISCLAIMER_RE = re.compile(
    r"not\s+(a\s+)?recommendation|not\s+investment\s+advice"
    r"|institutional\s+investors?\s+only"
    r"|risks\s+associated\s+with\s+investing"
    r"|does\s+not\s+constitute\s+(a\s+)?(recommendation|offer|solicitation)"
    r"|intended\s+for\s+(use\s+by\s+)?(institutional|qualified|professional)"
    r"|before\s+proceeding.*agree"
    r"|read\s+the\s+following\s+information\s+before"
    r"|conflict\s+of\s+interest"
    r"|past\s+performance\s+is\s+not\s+(a\s+)?(guarantee|indicative|reliable)",
    re.IGNORECASE,
)


def _count_article_paragraphs(content: str) -> int:
    blocks = re.split(r"\n{2,}", content)
    count = 0
    _boilerplate_kw = re.compile(
        r"TERMS AND CONDITIONS|INSTITUTIONAL USE ONLY|PLEASE READ|BY ENTERING THIS SITE"
        r"|YOU MUST READ|BEFORE PROCEEDING|COOKIE POLICY|PRIVACY POLICY"
        r"|Select a Role|Skip to",
        re.IGNORECASE,
    )
    for block in blocks:
        clean = _NAV_LINK_RE.sub("", block).strip()
        if len(clean) < 100:
            continue
        if clean == clean.upper() and len(clean) > 200:
            continue
        if _boilerplate_kw.search(clean[:200]):
            continue
        sentence_ends = sum(1 for c in clean if c in ".。!！?？")
        if sentence_ends >= 2:
            count += 1
    return count


_ROUNDUP_URL_RE = re.compile(
    r"top-\d+-\w+"
    r"|year-in-review"
    r"|best-of-\d{4}"
    r"|roundup"
    r"|highlights-of-\d{4}",
    re.IGNORECASE,
)


def _is_roundup_url(url: str) -> bool:
    return bool(_ROUNDUP_URL_RE.search(url))


def _is_junk_content(content: str) -> str | None:
    if len(content) < 500 and _ERROR_PAGE_RE.search(content):
        return "blocked_page"
    if len(content) < 5000 and _DISCLAIMER_RE.search(content[:500]):
        return "disclaimer"
    links = _NAV_LINK_RE.findall(content)
    n_links = len(links)
    disclaimer_hits = len(_INVEST_DISCLAIMER_RE.findall(content))
    if disclaimer_hits >= 3 and n_links > 15:
        return "disclaimer_index"
    n_paras = _count_article_paragraphs(content)
    if n_paras >= 3:
        return None
    if n_links > 15:
        return "nav_page"
    first_500 = content[:500].lower()
    menu_signals = sum(1 for kw in ("menu", "login", "register", "close menu", "sign in")
                       if kw in first_500)
    if menu_signals >= 2:
        return "nav_page"
    plain = _NAV_LINK_RE.sub("", content).strip()
    if len(plain) > 500:
        digit_space = sum(1 for c in plain if c.isdigit() or c in " ,.-\t\n")
        if digit_space > len(plain) * 0.7:
            return "raw_data"
    return None


# ── Sources 解析 ────────────────────────────────────────────────────────────

def load_sources() -> dict:
    with open(_find_sources(), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _platform_hint(platform_str: str) -> str:
    s = platform_str.lower()
    if "substack" in s:
        return "substack"
    if "youtube" in s or "yt" in s:
        return "youtube"
    if "bilibili" in s or "b站" in s:
        return "bilibili"
    if "weibo" in s or "微博" in s:
        return "weibo"
    if "linkedin" in s:
        return "linkedin"
    if "twitter" in s or "x.com" in s or "/x " in s:
        return "twitter"
    if "rss" in s or "feed" in s or "atom" in s:
        return "rss"
    return "generic"


def iter_fetchable_sources(sources_config: dict, name_filter: Optional[str] = None):
    for author in sources_config.get("authors", []):
        if name_filter and name_filter.lower() not in author["name"].lower():
            continue
        for src in author.get("sources", []):
            if not src.get("accessible", False):
                continue
            hint = _platform_hint(src.get("platform", ""))
            if hint in ("twitter", "linkedin"):
                continue
            yield author["name"], hint, src["url"], src.get("crawl_depth", 0), author["name"]

    for ch in sources_config.get("channels", []):
        if name_filter and name_filter.lower() not in ch["name"].lower():
            continue
        if not ch.get("accessible", False):
            continue
        hint = _platform_hint(ch.get("platform", ""))
        if hint in ("twitter", "linkedin"):
            continue
        yield ch["name"], hint, ch["url"], ch.get("crawl_depth", 0), None


# ── 已处理 URL 集合 ──────────────────────────────────────────────────────────

async def load_processed_urls() -> set[str]:
    from anchor.database.session import AsyncSessionLocal
    from anchor.models import RawPost
    from sqlmodel import select

    processed: set[str] = set()
    async with AsyncSessionLocal() as session:
        results = await session.exec(select(RawPost.url))
        for url in results:
            if url:
                processed.add(url)
    logger.info(f"Loaded {len(processed)} already-processed URLs from DB")
    return processed


# ── 提取结果 ──────────────────────────────────────────────────────────────────

@dataclass
class ExtractResult:
    url: str
    post_id: int | None
    reason: str
    author_hint: str | None


# ── 单 URL 提取 ──────────────────────────────────────────────────────────────

async def run_extraction(
    url: str,
    author_hint: Optional[str] = None,
    _allow_directory_crawl: bool = True,
    force: bool = False,
) -> list[ExtractResult]:
    from anchor.database.session import AsyncSessionLocal
    from anchor.collect.input_handler import process_url
    from anchor.chains.general_assessment import run_assessment
    from anchor.extract.extractor import Extractor
    from anchor.models import RawPost
    from sqlmodel import select

    extractor = Extractor()
    _skip = lambda reason: [ExtractResult(url=url, post_id=None, reason=reason, author_hint=author_hint)]

    try:
        async with AsyncSessionLocal() as s:
            result = await process_url(url, s)
        if not result or not result.raw_posts:
            logger.warning(f"  [extract] collect failed: {url}")
            return _skip("error")
        rp = result.raw_posts[0]
    except Exception as e:
        logger.error(f"  [extract] collect error: {e}")
        return _skip("error")

    _skip = lambda reason: [ExtractResult(url=url, post_id=rp.id, reason=reason, author_hint=author_hint)]

    if force and rp.assessed:
        try:
            async with AsyncSessionLocal() as s:
                _rp = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
                _rp.assessed = False
                _rp.assessed_at = None
                _rp.is_processed = False
                s.add(_rp)
                await s.commit()
            rp.assessed = False
            logger.info(f"  [extract] force: reset assessment for post {rp.id}")
        except Exception as e:
            logger.warning(f"  [extract] force reset failed: {e}")

    import json as _json
    _meta = {}
    try:
        _meta = _json.loads(rp.raw_metadata or "{}")
    except Exception:
        pass

    yt_redirect = _meta.get("youtube_redirect")
    if yt_redirect:
        logger.info(f"  [extract] YouTube redirect: {yt_redirect}")
        return await run_extraction(yt_redirect, author_hint=author_hint, _allow_directory_crawl=_allow_directory_crawl, force=force)
    if _meta.get("is_video_only"):
        logger.info(f"  [extract] video/wrapper page, skip: {url}")
        return _skip("video_only")

    _duration_s = _meta.get("duration_s") or 0
    if _duration_s and _duration_s < 180:
        logger.info(f"  [extract] short video ({_duration_s}s < 180s), skip: {url}")
        return _skip("video_short")

    _content_chars = len((rp.content or "").strip())
    if _content_chars < 200:
        logger.info(f"  [extract] content too short ({_content_chars} chars), skip: {url}")
        return _skip("text_short")

    if _is_paywalled(rp.content or ""):
        logger.info(f"  [extract] paywall detected, skip: {url}")
        return _skip("paywall_skip")

    if _is_roundup_url(url):
        logger.info(f"  [extract] roundup/listicle URL, skip: {url}")
        return _skip("junk_skip")

    _junk_reason = _is_junk_content(rp.content or "")
    if _junk_reason:
        if _allow_directory_crawl and _junk_reason in ("nav_page", "disclaimer_index"):
            from anchor.monitor.index_crawler import crawl_index_page
            sub_items = await crawl_index_page(
                url, "auto-crawl", max_depth=1, processed_urls=set(),
            )
            if sub_items:
                logger.info(f"  [extract] directory detected at {url}, crawling {len(sub_items)} sub-articles")
                try:
                    async with AsyncSessionLocal() as s:
                        dir_post = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
                        if dir_post:
                            await s.delete(dir_post)
                            await s.commit()
                            logger.info(f"  [extract] removed directory RawPost (id={rp.id}) for re-check next run")
                except Exception as e:
                    logger.warning(f"  [extract] failed to remove directory RawPost: {e}")
                results: list[ExtractResult] = []
                for item in sub_items:
                    sub = await run_extraction(item.url, author_hint=author_hint, _allow_directory_crawl=False, force=force)
                    results.extend(sub)
                if results:
                    return results
        logger.info(f"  [extract] junk content ({_junk_reason}), skip: {url}")
        return _skip("junk_skip")

    try:
        async with AsyncSessionLocal() as s:
            pre = await run_assessment(rp.id, s, author_hint=author_hint)
        ct = pre.get("content_type", "")
        from anchor.chains.general_assessment import resolve_content_mode
        content_mode = resolve_content_mode(
            pre.get("content_domain"), pre.get("content_nature"), ct,
        )
    except Exception as e:
        logger.error(f"  [extract] assessment error: {e}")
        content_mode = "standard"
        pre = {}
        ct = ""

    _ALLOWED_CONTENT_TYPES = {"财经分析", "市场动向", "产业链研究", "公司调研", "政策解读", "技术论文", "公司财报"}
    if ct and ct not in _ALLOWED_CONTENT_TYPES:
        logger.info(f"  [extract] content_type={ct!r} (非目标类型), skip: {url}")
        return _skip("non_market")

    try:
        async with AsyncSessionLocal() as s:
            rp3 = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
            _extraction_result = await extractor.extract(
                rp3, s,
                content_mode=content_mode,
                author_intent=pre.get("author_intent"),
            )
        if _extraction_result is not None and not _extraction_result.is_relevant_content:
            logger.info(f"  [extract] content not relevant ({_extraction_result.skip_reason}), skip: {url}")
            return _skip("not_relevant")
    except Exception as e:
        logger.error(f"  [extract] extract error: {e}")

    return [ExtractResult(url=url, post_id=rp.id, reason="extracted", author_hint=author_hint)]


async def _write_notion(result: ExtractResult) -> str:
    if not result.post_id:
        return "notion_skip"
    try:
        from anchor.notion_sync import sync_post_to_notion
        from anchor.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            notion_url = await sync_post_to_notion(result.post_id, s)
            await s.commit()
        if notion_url:
            logger.info(f"  [notion] {notion_url}")
            return "written"
        else:
            logger.info(f"  [notion] skipped (content_type not mapped)")
            return "notion_skip"
    except Exception as e:
        logger.error(f"  [notion] sync error: {e}")
        return "notion_skip"


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def _main(
    dry_run: bool,
    source: Optional[str],
    limit: int,
    concurrency: int,
    since: Optional[datetime],
    force: bool,
) -> None:
    from anchor.monitor.feed_fetcher import fetch_source

    _since = since or DEFAULT_SINCE
    logger.info(f"Date cutoff: {_since.date()} (只抓取此日期之后的文章)")

    sources_config = load_sources()
    processed_urls = await load_processed_urls()
    if force:
        logger.info("Force mode: 重新处理所有 URL（忽略已处理标记）")

    sources = list(iter_fetchable_sources(sources_config, name_filter=source))
    logger.info(f"Sources to check: {len(sources)}")

    all_items: list[tuple[str, str | None, str]] = []
    total_new = 0
    total_capped = 0

    for display_name, hint, src_url, crawl_depth, author_name in sources:
        logger.info(f"\n── {display_name} [{hint}] {src_url}")

        try:
            if crawl_depth > 0:
                from anchor.monitor.index_crawler import crawl_index_page
                items = await crawl_index_page(
                    src_url, display_name, max_depth=crawl_depth,
                    processed_urls=processed_urls,
                )
            else:
                items = fetch_source(hint, src_url, since=_since)
        except Exception as e:
            logger.error(f"  fetch error: {e}")
            continue

        if force:
            new_items = items
        else:
            new_items = [it for it in items if it.url not in processed_urls]
        logger.info(f"  {len(items)} fetched, {len(new_items)} {'total (force)' if force else 'new'}")

        if dry_run:
            for it in new_items:
                print(f"    [DRY-RUN] {it.url}  「{it.title[:60]}」")
            total_new += len(new_items)
            continue

        to_process = new_items[:limit] if limit else new_items
        capped = len(new_items) - len(to_process)
        total_new += len(new_items)
        total_capped += capped

        for it in to_process:
            label = f"{display_name}: {it.title[:60]}"
            all_items.append((it.url, author_name, label))
            processed_urls.add(it.url)

    if dry_run:
        logger.info(f"\n══ 完成：发现新内容 {total_new} 条（dry-run）")
        return

    if not all_items:
        logger.info(f"\n══ 完成：无新内容需要处理")
        return

    logger.info(f"\n══ 开始并行处理 {len(all_items)} 条（concurrency={concurrency}）")

    queue: asyncio.Queue[ExtractResult | None] = asyncio.Queue()
    sem = asyncio.Semaphore(concurrency)
    skip_counts: Counter = Counter()

    async def notion_consumer():
        while True:
            result = await queue.get()
            if result is None:
                break
            if result.reason == "extracted":
                notion_reason = await _write_notion(result)
                skip_counts[notion_reason] += 1
            else:
                skip_counts[result.reason] += 1
            queue.task_done()

    async def extract_worker(url: str, author_hint: str | None, label: str):
        async with sem:
            logger.info(f"  → {label}")
            results = await run_extraction(url, author_hint=author_hint, force=force)
            for result in results:
                await queue.put(result)

    consumer_task = asyncio.create_task(notion_consumer())
    producers = [
        asyncio.create_task(extract_worker(url, author_hint, label))
        for url, author_hint, label in all_items
    ]
    await asyncio.gather(*producers)
    await queue.put(None)
    await consumer_task

    written = skip_counts.pop("written", 0)
    lines = [f"\n══ 完成：发现新内容 {total_new} 条，处理 {sum(skip_counts.values()) + written} 条，写入 Notion {written} 条"]
    if total_capped:
        lines.append(f"  限速截断（--limit）: {total_capped} 条未处理")
    _labels = {
        "notion_skip":   "类型未映射",
        "non_market":    "非目标类型",
        "not_relevant":  "内容无关",
        "text_short":    "文章过短",
        "video_short":   "视频过短",
        "video_only":    "纯视频页",
        "paywall_skip":  "付费墙跳过",
        "junk_skip":     "非文章页",
        "error":         "采集失败",
    }
    for key, label in _labels.items():
        if skip_counts[key]:
            lines.append(f"  {label}: {skip_counts[key]} 条")
    logger.info("\n".join(lines))


# ── CLI 入口 ────────────────────────────────────────────────────────────────

def monitor_command(
    dry_run: bool = False,
    source: Optional[str] = None,
    limit: int = 0,
    concurrency: int = 5,
    since: Optional[str] = None,
    force: bool = False,
) -> None:
    """CLI 入口，由 anchor.cli 调用。"""
    since_dt: Optional[datetime] = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    asyncio.run(_main(
        dry_run=dry_run,
        source=source,
        limit=limit,
        concurrency=concurrency,
        since=since_dt,
        force=force,
    ))
