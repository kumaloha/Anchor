"""
run_monitor.py — Anchor 订阅监控主脚本
========================================
从 watchlist.yaml 读取所有可自动抓取的来源，
拉取新内容 URL，喂入 Anchor 分析流水线并写入 Notion。

用法：
    python run_monitor.py              # 跑全部来源
    python run_monitor.py --dry-run    # 仅列出新 URL，不分析
    python run_monitor.py --source "Howard Marks"   # 仅跑指定名称
    python run_monitor.py --limit 5    # 每个来源最多处理 N 条新内容

依赖：
    pip install feedparser yt-dlp httpx pyyaml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_ui.db")

# 只抓取此日期之后发布的内容（UTC）
DEFAULT_SINCE = datetime(2026, 3, 1, tzinfo=timezone.utc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("monitor")

WATCHLIST_PATH = Path(__file__).parent / "watchlist.yaml"


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
    r"|付费(阅读|查看)全文",
    re.IGNORECASE,
)


def _is_paywalled(content: str) -> bool:
    return bool(_PAYWALL_RE.search(content))


# ── Watchlist 解析 ────────────────────────────────────────────────────────────

def load_watchlist() -> dict:
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _platform_hint(platform_str: str) -> str:
    """从 platform 字段推断平台 key（小写，去除展示性描述）。"""
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


def iter_fetchable_sources(watchlist: dict, name_filter: Optional[str] = None):
    """
    生成器：遍历 authors + channels 下所有 accessible=true 的来源。
    Yields (display_name, platform_hint, url)
    """
    for author in watchlist.get("authors", []):
        if name_filter and name_filter.lower() not in author["name"].lower():
            continue
        for src in author.get("sources", []):
            if not src.get("accessible", False):
                continue
            hint = _platform_hint(src.get("platform", ""))
            # 跳过不支持自动抓取的平台
            if hint in ("twitter", "linkedin"):
                continue
            yield author["name"], hint, src["url"]

    for ch in watchlist.get("channels", []):
        if name_filter and name_filter.lower() not in ch["name"].lower():
            continue
        if not ch.get("accessible", False):
            continue
        hint = _platform_hint(ch.get("platform", ""))
        if hint in ("twitter", "linkedin"):
            continue
        yield ch["name"], hint, ch["url"]


# ── 已处理 URL 集合（内存内去重）─────────────────────────────────────────────

async def load_processed_urls() -> set[str]:
    """从数据库加载所有已处理过的 RawPost.url。"""
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


# ── 单 URL 全流水线 ───────────────────────────────────────────────────────────

async def run_pipeline(url: str) -> str:
    """
    对单条 URL 执行完整 Anchor 流水线：
      process_url → run_chain2 → extractor.extract → sync_post_to_notion

    返回原因字符串：
      "written"      — 成功写入 Notion
      "notion_skip"  — LLM 提取完成但 content_type 未映射到 Notion
      "video_only"   — 纯视频占位页（无实质文字）
      "video_short"  — 视频 < 5 分钟
      "text_short"   — 文章正文 < 200 字
      "non_market"   — 内容类型非市场分析
      "error"        — 采集 / LLM 调用失败
    """
    from anchor.database.session import AsyncSessionLocal
    from anchor.collect.input_handler import process_url
    from anchor.chains.chain2_author import run_chain2
    from anchor.extract.extractor import Extractor
    from anchor.models import RawPost
    from sqlmodel import select

    extractor = Extractor()

    # Step 1: 采集
    try:
        async with AsyncSessionLocal() as s:
            result = await process_url(url, s)
        if not result or not result.raw_posts:
            logger.warning(f"  [pipeline] collect failed: {url}")
            return "error"
        rp = result.raw_posts[0]
    except Exception as e:
        logger.error(f"  [pipeline] collect error: {e}")
        return "error"

    # ── 内容质量检查：纯视频页 / YouTube 跳转 / 内容过短 ────────────────────
    import json as _json
    _meta = {}
    try:
        _meta = _json.loads(rp.raw_metadata or "{}")
    except Exception:
        pass

    yt_redirect = _meta.get("youtube_redirect")
    if _meta.get("is_video_only"):
        if yt_redirect:
            logger.info(f"  [pipeline] video page → YouTube redirect: {yt_redirect}")
            return await run_pipeline(yt_redirect)   # 递归：改抓 YouTube 视频
        else:
            logger.info(f"  [pipeline] video/wrapper page, skip: {url}")
            return "video_only"

    _duration_s = _meta.get("duration_s") or 0
    if _duration_s and _duration_s < 180:
        logger.info(f"  [pipeline] short video ({_duration_s}s < 180s), skip: {url}")
        return "video_short"

    _content_chars = len((rp.content or "").strip())
    if _content_chars < 200:
        logger.info(f"  [pipeline] content too short ({_content_chars} chars), skip: {url}")
        return "text_short"

    if _is_paywalled(rp.content or ""):
        logger.info(f"  [pipeline] paywall detected, skip: {url}")
        return "paywall_skip"

    # Step 2: Chain 2
    try:
        async with AsyncSessionLocal() as s:
            pre = await run_chain2(rp.id, s)
        ct = pre.get("content_type", "")
        content_mode = "policy" if ct == "政策解读" else "standard"
    except Exception as e:
        logger.error(f"  [pipeline] chain2 error: {e}")
        content_mode = "standard"
        pre = {}
        ct = ""

    # 只处理财经分析类内容，其余跳过
    if ct and ct != "财经分析":
        logger.info(f"  [pipeline] content_type={ct!r} (非财经分析), skip: {url}")
        return "non_market"

    # Step 3: Chain 1 提取
    try:
        async with AsyncSessionLocal() as s:
            rp3 = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
            await extractor.extract(
                rp3, s,
                content_mode=content_mode,
                author_intent=pre.get("author_intent"),
            )
    except Exception as e:
        logger.error(f"  [pipeline] extract error: {e}")

    # Step 4: Notion 同步
    try:
        from anchor.notion_sync import sync_post_to_notion
        async with AsyncSessionLocal() as s:
            notion_url = await sync_post_to_notion(rp.id, s)
        if notion_url:
            logger.info(f"  [pipeline] Notion: {notion_url}")
            return "written"
        else:
            logger.info(f"  [pipeline] Notion skipped (content_type not mapped)")
            return "notion_skip"
    except Exception as e:
        logger.error(f"  [pipeline] notion sync error: {e}")
        return "notion_skip"


# ── 主流程 ────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    from anchor.monitor.feed_fetcher import fetch_source

    since: Optional[datetime] = args.since or DEFAULT_SINCE
    logger.info(f"Date cutoff: {since.date()} (只抓取此日期之后的文章)")

    watchlist = load_watchlist()
    processed_urls = await load_processed_urls()

    sources = list(iter_fetchable_sources(watchlist, name_filter=args.source))
    logger.info(f"Sources to check: {len(sources)}")

    total_new = 0
    total_capped = 0   # 被 --limit 截断未处理
    from collections import Counter
    skip_counts: Counter = Counter()

    for display_name, hint, src_url in sources:
        logger.info(f"\n── {display_name} [{hint}] {src_url}")

        try:
            items = fetch_source(hint, src_url, since=since)
        except Exception as e:
            logger.error(f"  fetch error: {e}")
            continue

        # 过滤已处理
        new_items = [it for it in items if it.url not in processed_urls]
        logger.info(f"  {len(items)} fetched, {len(new_items)} new")

        if args.dry_run:
            for it in new_items:
                print(f"    [DRY-RUN] {it.url}  「{it.title[:60]}」")
            total_new += len(new_items)
            continue

        # 限速：每个来源最多处理 args.limit 条
        to_process = new_items[:args.limit] if args.limit else new_items
        capped = len(new_items) - len(to_process)
        total_new += len(new_items)
        total_capped += capped

        for it in to_process:
            logger.info(f"  → {it.url}  「{it.title[:60]}」")
            reason = await run_pipeline(it.url)
            skip_counts[reason] += 1
            processed_urls.add(it.url)
            # 短暂间隔，避免 API 速率限制
            await asyncio.sleep(2)

    if not args.dry_run:
        written = skip_counts.pop("written", 0)
        lines = [f"\n══ 完成：发现新内容 {total_new} 条，处理 {sum(skip_counts.values()) + written} 条，写入 Notion {written} 条"]
        if total_capped:
            lines.append(f"  限速截断（--limit）: {total_capped} 条未处理")
        _labels = {
            "notion_skip":   "类型未映射",
            "non_market":    "非财经分析",
            "text_short":    "文章过短",
            "video_short":   "视频过短",
            "video_only":    "纯视频页",
            "paywall_skip":  "付费墙跳过",
            "error":         "采集失败",
        }
        for key, label in _labels.items():
            if skip_counts[key]:
                lines.append(f"  {label}: {skip_counts[key]} 条")
        logger.info("\n".join(lines))
    else:
        logger.info(f"\n══ 完成：发现新内容 {total_new} 条（dry-run）")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def _parse_since(s: str) -> datetime:
    """解析 --since 参数，格式：YYYY-MM-DD"""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Anchor 订阅监控 — 从 watchlist.yaml 批量拉取新文章并写入 Notion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_monitor.py                        # 跑全部来源（不限条数）
  python run_monitor.py --dry-run              # 仅预览新 URL，不分析
  python run_monitor.py --source "Robin Brooks"  # 仅跑指定作者
  python run_monitor.py --limit 0              # 不限条数
  python run_monitor.py --since 2026-02-01     # 自定义日期截止
        """,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="仅列出新 URL，不执行分析流水线")
    parser.add_argument("--source", default=None, metavar="NAME",
                        help="仅处理名称含该字符串的来源")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="每个来源最多处理新条目数（默认 0=不限）")
    parser.add_argument("--since", type=_parse_since, default=None, metavar="YYYY-MM-DD",
                        help=f"只抓此日期之后的文章（默认 {DEFAULT_SINCE.date()}）")
    args = parser.parse_args()

    asyncio.run(main(args))
