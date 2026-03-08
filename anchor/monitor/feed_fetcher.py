"""
anchor/monitor/feed_fetcher.py
──────────────────────────────
平台专属 URL 抓取器：
  - RSS / Atom（Substack、Project Syndicate、IMF、Fed、ECB…）→ feedparser
  - YouTube 频道 → yt-dlp 平铺列表
  - Bilibili 空间 → 官方 API
  - Weibo 用户 → 简单 HTML 抓取（仅公开微博）
  - LinkedIn / Twitter → 暂不支持自动抓取（返回空列表，需人工）

返回值统一为 list[FetchedItem]：
    url           str   — 可直接喂给 process_url() 的完整链接
    title         str   — 标题（可能为空）
    published_at  datetime | None
    raw_id        str   — 平台侧唯一 ID，用于幂等去重
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass
class FetchedItem:
    url: str
    title: str = ""
    published_at: Optional[datetime] = None
    raw_id: str = ""


# ── RSS / Atom ────────────────────────────────────────────────────────────────

def _as_utc(dt: datetime) -> datetime:
    """将 datetime 统一转成 UTC（无 tzinfo 的视为 UTC）。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(val: str | None) -> Optional[datetime]:
    if not val:
        return None
    try:
        return parsedate_to_datetime(val)
    except Exception:
        try:
            import feedparser  # type: ignore
            t = feedparser._parse_date(val)  # type: ignore[attr-defined]
            if t:
                return datetime(*t[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_rss(feed_url: str, since: Optional[datetime] = None) -> list[FetchedItem]:
    """通用 RSS/Atom 抓取。"""
    try:
        import feedparser  # type: ignore
    except ImportError:
        logger.warning("feedparser not installed; skipping RSS fetch")
        return []

    logger.info(f"[RSS] Fetching {feed_url}")
    d = feedparser.parse(feed_url)
    if d.get("bozo") and not d.get("entries"):
        logger.warning(f"[RSS] Feed parse error for {feed_url}: {d.get('bozo_exception')}")
        return []

    items: list[FetchedItem] = []
    for entry in d.entries:
        link = entry.get("link", "")
        if not link:
            continue

        pub_str = entry.get("published") or entry.get("updated") or ""
        pub_dt = _parse_dt(pub_str)

        if since and pub_dt and _as_utc(pub_dt) <= _as_utc(since):
            continue

        raw_id = entry.get("id") or link
        title = entry.get("title", "")
        items.append(FetchedItem(url=link, title=title, published_at=pub_dt, raw_id=raw_id))

    logger.info(f"[RSS] {len(items)} new items from {feed_url}")
    return items


# ── Substack（RSS 内置）────────────────────────────────────────────────────────

def substack_rss_url(base_url: str) -> str:
    """将 Substack 博客主页 URL 转成 RSS 地址。"""
    base = base_url.rstrip("/")
    if "/feed" in base:
        return base
    return base + "/feed"


# ── YouTube（yt-dlp 平铺列表）─────────────────────────────────────────────────

def fetch_youtube_channel(channel_url: str, since: Optional[datetime] = None,
                          max_results: int = 20) -> list[FetchedItem]:
    """用 yt-dlp 获取 YouTube 频道最新视频列表（不下载）。"""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        logger.warning("yt-dlp not installed; skipping YouTube fetch")
        return []

    # 确保 URL 指向视频列表
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,       # 只要元数据，不下载
        "playlistend": max_results,
        "ignoreerrors": True,
    }

    logger.info(f"[YouTube] Fetching channel: {url}")
    items: list[FetchedItem] = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            entries = info.get("entries") or []
            for e in entries:
                if not e:
                    continue
                vid_id = e.get("id") or e.get("url", "")
                if not vid_id:
                    continue
                vid_url = f"https://www.youtube.com/watch?v={vid_id}"
                title = e.get("title", "")
                # upload_date: YYYYMMDD string
                upload_date = e.get("upload_date") or e.get("timestamp")
                pub_dt: Optional[datetime] = None
                if isinstance(upload_date, str) and len(upload_date) == 8:
                    try:
                        pub_dt = datetime(
                            int(upload_date[:4]),
                            int(upload_date[4:6]),
                            int(upload_date[6:8]),
                            tzinfo=timezone.utc,
                        )
                    except Exception:
                        pass
                elif isinstance(upload_date, (int, float)):
                    try:
                        pub_dt = datetime.fromtimestamp(upload_date, tz=timezone.utc)
                    except Exception:
                        pass

                if since and pub_dt and pub_dt <= since.replace(tzinfo=timezone.utc):
                    continue

                # 跳过短视频（< 3 分钟 = 180 秒）
                duration = e.get("duration") or 0
                if duration and duration < 180:
                    logger.info(f"[YouTube] Skip short video ({duration}s < 180s): {title!r}")
                    continue

                items.append(FetchedItem(url=vid_url, title=title, published_at=pub_dt, raw_id=vid_id))
    except Exception as e:
        logger.error(f"[YouTube] Error fetching {url}: {e}")

    logger.info(f"[YouTube] {len(items)} new items from {url}")
    return items


# ── Bilibili（官方开放 API）───────────────────────────────────────────────────

def fetch_bilibili_space(uid: str, since: Optional[datetime] = None,
                         max_results: int = 20) -> list[FetchedItem]:
    """通过 B 站公开 API 获取 UP 主最新投稿。"""
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping Bilibili fetch")
        return []

    api_url = (
        f"https://api.bilibili.com/x/space/arc/search"
        f"?mid={uid}&ps={max_results}&pn=1&order=pubdate"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://space.bilibili.com/",
    }

    logger.info(f"[Bilibili] Fetching uid={uid}")
    items: list[FetchedItem] = []
    try:
        resp = httpx.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        for v in vlist:
            bvid = v.get("bvid", "")
            if not bvid:
                continue
            vid_url = f"https://www.bilibili.com/video/{bvid}"
            title = v.get("title", "")
            created = v.get("created")  # Unix timestamp
            pub_dt: Optional[datetime] = None
            if created:
                try:
                    pub_dt = datetime.fromtimestamp(int(created), tz=timezone.utc)
                except Exception:
                    pass

            if since and pub_dt and pub_dt <= since.replace(tzinfo=timezone.utc):
                continue

            items.append(FetchedItem(url=vid_url, title=title, published_at=pub_dt, raw_id=bvid))
    except Exception as e:
        logger.error(f"[Bilibili] Error fetching uid={uid}: {e}")

    logger.info(f"[Bilibili] {len(items)} new items for uid={uid}")
    return items


# ── Weibo（公开帖子，轻量 HTML 抓取）─────────────────────────────────────────

def fetch_weibo_user(profile_url: str, since: Optional[datetime] = None,
                     max_results: int = 10) -> list[FetchedItem]:
    """
    从微博用户主页抓取最新微博 URL。
    仅抓取公开内容，速率严格控制。
    注意：微博反爬较强，这里用简单方式尝试；如被封锁会返回空列表。
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping Weibo fetch")
        return []

    # 从 profile URL 提取 uid
    uid_match = re.search(r"weibo\.com/(\d+)", profile_url)
    if not uid_match:
        logger.warning(f"[Weibo] Cannot extract uid from {profile_url}")
        return []
    uid = uid_match.group(1)

    # 微博开放 API（无需登录的基本信息）
    api_url = f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://weibo.com/{uid}",
        "Accept": "application/json, text/plain, */*",
    }

    logger.info(f"[Weibo] Fetching uid={uid}")
    items: list[FetchedItem] = []
    try:
        resp = httpx.get(api_url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        statuses = data.get("data", {}).get("list", [])
        for st in statuses[:max_results]:
            mid = st.get("id") or st.get("mid", "")
            if not mid:
                continue
            # 微博详情 URL
            bid = st.get("bid") or mid
            post_url = f"https://weibo.com/{uid}/{bid}"
            title = (st.get("text") or "")[:80]  # 正文截断作为标题
            created_at_str = st.get("created_at", "")
            pub_dt = _parse_dt(created_at_str) if created_at_str else None

            if since and pub_dt and _as_utc(pub_dt) <= _as_utc(since):
                continue

            items.append(FetchedItem(url=post_url, title=title, published_at=pub_dt, raw_id=str(mid)))
            time.sleep(0.3)  # 礼貌延时
    except Exception as e:
        logger.error(f"[Weibo] Error fetching uid={uid}: {e}")

    logger.info(f"[Weibo] {len(items)} new items for uid={uid}")
    return items


# ── 平台路由 ──────────────────────────────────────────────────────────────────

def _detect_platform(url: str) -> str:
    """从 URL 猜测平台类型。"""
    u = url.lower()
    if "substack.com" in u:
        return "substack"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "bilibili.com" in u or "space.bilibili" in u:
        return "bilibili"
    if "weibo.com" in u:
        return "weibo"
    if "linkedin.com" in u:
        return "linkedin"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    return "generic"


# 已知支持 RSS 的域名（可直接附加 /feed 或已有 RSS）
_RSS_DOMAINS = {
    "project-syndicate.org",
    "brookings.edu",
    "piie.com",
    "imf.org",
    "federalreserve.gov",
    "ecb.europa.eu",
    "bankofengland.co.uk",
    "boj.or.jp",
    "bis.org",
    "pbc.gov.cn",
    "nber.org",
    "goldmansachs.com",
    "morganstanley.com",
    "oaktreecapital.com",
    "blackstone.com",
    "ssga.com",
    "citadel.com",
    "doubleline.com",
    "opensocietyfoundations.org",
    "paulkrugman.substack.com",
    "roubini.substack.com",
    "jeffsachs.substack.com",
    "robinjbrooks.substack.com",
}


def fetch_source(platform_hint: str, url: str,
                 since: Optional[datetime] = None) -> list[FetchedItem]:
    """
    统一入口：根据 platform_hint（来自 watchlist.yaml）选择抓取方式。

    platform_hint 可能为：
      "substack", "rss", "youtube", "bilibili", "weibo",
      "linkedin", "twitter", "generic"
      以及任意自定义字符串（模糊匹配）
    """
    hint = (platform_hint or "").lower()

    # ── 子平台精确路由 ────────────────────────────────────────────────────────
    if hint in ("substack",):
        rss_url = substack_rss_url(url)
        return fetch_rss(rss_url, since=since)

    if hint in ("youtube",):
        return fetch_youtube_channel(url, since=since)

    if hint in ("bilibili",):
        # URL: https://space.bilibili.com/UID 或 https://www.bilibili.com/video/...
        uid_match = re.search(r"space\.bilibili\.com/(\d+)", url)
        if uid_match:
            return fetch_bilibili_space(uid_match.group(1), since=since)
        logger.warning(f"[Bilibili] Cannot extract uid from {url}")
        return []

    if hint in ("weibo",):
        return fetch_weibo_user(url, since=since)

    if hint in ("linkedin", "twitter", "x"):
        logger.info(f"[Monitor] Platform '{hint}' does not support auto-fetch; skip")
        return []

    # ── RSS 提示或通用域名匹配 ────────────────────────────────────────────────
    if hint in ("rss", "atom", "feed"):
        return fetch_rss(url, since=since)

    # ── 通用：尝试从域名判断 ──────────────────────────────────────────────────
    detected = _detect_platform(url)
    if detected != "generic":
        return fetch_source(detected, url, since=since)

    # 检查是否是已知 RSS 域名
    domain = urlparse(url).netloc.lstrip("www.")
    for rss_domain in _RSS_DOMAINS:
        if domain.endswith(rss_domain):
            # 尝试附加 /feed
            rss_url = url.rstrip("/") + "/feed"
            items = fetch_rss(rss_url, since=since)
            if items:
                return items
            # 原 URL 本身可能就是 RSS
            return fetch_rss(url, since=since)

    # 最终兜底：直接尝试原 URL 作为 RSS feed
    logger.info(f"[Monitor] Generic fetch attempt for {url}")
    return fetch_rss(url, since=since)
