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

import os
import re
import time
import logging
import html as _html
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlparse

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


_RSS_UA = "Mozilla/5.0 (compatible; AnchorFeedBot/1.0; +https://github.com/anchor)"
_GENERIC_UA = "Mozilla/5.0 (compatible; AnchorGenericBot/1.0; +https://github.com/anchor)"


def fetch_rss(feed_url: str, since: Optional[datetime] = None) -> list[FetchedItem]:
    """通用 RSS/Atom 抓取（携带 User-Agent，避免 403）。"""
    try:
        import feedparser  # type: ignore
    except ImportError:
        logger.warning("feedparser not installed; skipping RSS fetch")
        return []

    logger.info(f"[RSS] Fetching {feed_url}")

    # 先用 httpx 下载（带 User-Agent），再交给 feedparser 解析
    # 直接 feedparser.parse(url) 因无 UA 常被 403
    try:
        import httpx as _httpx
        resp = _httpx.get(
            feed_url, follow_redirects=True, timeout=20,
            headers={"User-Agent": _RSS_UA, "Accept": "application/rss+xml, application/atom+xml, */*"},
        )
        if resp.status_code >= 400:
            logger.warning(f"[RSS] HTTP {resp.status_code} for {feed_url}")
            return []
        d = feedparser.parse(resp.text)
    except Exception as exc:
        logger.warning(f"[RSS] Download failed for {feed_url}: {exc}")
        # 回退：直接让 feedparser 尝试
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


# ── HTML 列表页兜底（无 RSS 时提取文章链接）────────────────────────────────────

_A_TAG_RE = re.compile(
    r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_DATE_IN_PATH_RE = re.compile(r"/20\d{2}(?:/|[-_])")
_STATIC_EXT_RE = re.compile(
    r"\.(?:css|js|png|jpg|jpeg|gif|webp|svg|ico|zip|mp3|mp4|woff2?|ttf)(?:$|\?)",
    flags=re.IGNORECASE,
)
_NEGATIVE_HINTS = (
    "/about", "/contact", "/privacy", "/terms", "/cookie", "/careers",
    "/login", "/signin", "/signup", "/account", "/search", "/tag/",
    "/tags/", "/category/", "/categories/", "/author/", "/authors/",
    "/wp-json", "/cdn-cgi",
)
_POSITIVE_HINTS = (
    "insight", "insights", "blog", "news", "article", "research",
    "report", "speech", "commentary", "memo", "publication", "letter",
    "letters", "opinion", "post", "interview", "viewpoint",
)


def _strip_html(text: str) -> str:
    s = _TAG_RE.sub(" ", text or "")
    s = _html.unescape(s)
    return _SPACE_RE.sub(" ", s).strip()


def _is_same_site(base_netloc: str, candidate_netloc: str) -> bool:
    b = base_netloc.lower().lstrip("www.")
    c = candidate_netloc.lower().lstrip("www.")
    return c == b or c.endswith("." + b) or b.endswith("." + c)


def _score_candidate(list_path: str, link_url: str, anchor_text: str) -> int:
    p = urlparse(link_url)
    path = p.path.lower()
    score = 0

    if _DATE_IN_PATH_RE.search(path):
        score += 3
    if any(k in path for k in _POSITIVE_HINTS):
        score += 3
    if "-" in path.rsplit("/", 1)[-1]:
        score += 2

    segs = [s for s in path.split("/") if s]
    if len(segs) >= 2:
        score += 1
    if segs and len(segs[-1]) >= 8:
        score += 1

    if any(k in path for k in _NEGATIVE_HINTS):
        score -= 4
    if path.rstrip("/") == list_path.rstrip("/").lower():
        score -= 3
    if _STATIC_EXT_RE.search(path):
        score -= 5

    at = (anchor_text or "").lower()
    if len(anchor_text) >= 8:
        score += 1
    if any(k in at for k in ("read more", "learn more", "more")):
        score -= 1

    return score


def fetch_generic_links(page_url: str, since: Optional[datetime] = None,
                        max_results: int = 200) -> list[FetchedItem]:
    """从非 RSS 列表页抽取可能的文章链接。"""
    del since  # HTML 列表页通常缺失发布时间，无法按 since 过滤
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping generic fetch")
        return []

    logger.info(f"[Generic] Fetching {page_url}")
    try:
        resp = httpx.get(
            page_url,
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": _GENERIC_UA, "Accept": "text/html,application/xhtml+xml,*/*"},
        )
    except Exception as exc:
        logger.warning(f"[Generic] Download failed for {page_url}: {exc}")
        return []

    if resp.status_code >= 400:
        logger.warning(f"[Generic] HTTP {resp.status_code} for {page_url}")
        return []

    base_url = str(resp.url)
    base = urlparse(base_url)
    html = resp.text or ""
    if not html.strip():
        return []

    candidates: list[tuple[int, str, str]] = []
    for href, inner in _A_TAG_RE.findall(html):
        h = (href or "").strip()
        if not h:
            continue
        h_lower = h.lower()
        if h_lower.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        abs_url = urljoin(base_url, h)
        abs_url, _ = urldefrag(abs_url)
        p = urlparse(abs_url)
        if p.scheme not in ("http", "https"):
            continue
        if not _is_same_site(base.netloc, p.netloc):
            continue

        anchor_text = _strip_html(inner)
        score = _score_candidate(base.path, abs_url, anchor_text)
        if score < 3:
            continue
        candidates.append((score, abs_url, anchor_text))

    seen: set[str] = set()
    items: list[FetchedItem] = []
    for score, link_url, anchor_text in sorted(candidates, key=lambda x: (-x[0], x[1])):
        if link_url in seen:
            continue
        seen.add(link_url)
        title = anchor_text[:180] if anchor_text else ""
        items.append(FetchedItem(url=link_url, title=title, published_at=None, raw_id=link_url))
        if len(items) >= max_results:
            break

    logger.info(f"[Generic] {len(items)} candidate links from {page_url}")
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

    # 播放列表 URL 直接使用；频道 URL 加 /videos
    url = channel_url.rstrip("/")
    if "playlist?list=" not in url and not url.endswith("/videos"):
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


# ── Bilibili（wbi 签名 API）───────────────────────────────────────────────────

_BILI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18,  2, 53,  8, 23, 32, 15, 50, 10, 31, 58,  3, 45, 35,
    27, 43,  5, 49, 33,  9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48,  7, 16, 24, 55, 40, 61, 26, 17,  0,  1, 60, 51, 30,  4,
    22, 25, 54, 21, 56, 59,  6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_bili_wbi_cache: dict = {}


def _bili_get_mixin_key(orig: str) -> str:
    from functools import reduce
    return reduce(lambda s, i: s + orig[i], _BILI_MIXIN_KEY_ENC_TAB, "")[:32]


def _bili_get_wbi_keys() -> tuple[str, str]:
    """从 nav API 获取 wbi img_key + sub_key（缓存 24h）。"""
    import httpx as _httpx
    now = time.time()
    if _bili_wbi_cache and now - _bili_wbi_cache.get("ts", 0) < 86400:
        return _bili_wbi_cache["img_key"], _bili_wbi_cache["sub_key"]

    resp = _httpx.get(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        },
        timeout=10,
    )
    data = resp.json()["data"]["wbi_img"]
    img_key = data["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = data["sub_url"].rsplit("/", 1)[1].split(".")[0]
    _bili_wbi_cache.update(img_key=img_key, sub_key=sub_key, ts=now)
    return img_key, sub_key


def _bili_sign_wbi(params: dict) -> dict:
    """对请求参数进行 wbi 签名，返回带 wts + w_rid 的新参数字典。"""
    from hashlib import md5
    from urllib.parse import urlencode

    img_key, sub_key = _bili_get_wbi_keys()
    mixin_key = _bili_get_mixin_key(img_key + sub_key)

    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {
        k: "".join(c for c in str(v) if c not in "!'()*")
        for k, v in params.items()
    }
    query = urlencode(params)
    params["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return params


def _get_bilibili_cookies() -> dict[str, str]:
    """从环境变量 BILIBILI_COOKIE 获取 B 站 cookies。

    格式：BILIBILI_COOKIE="SESSDATA=xxx; bili_jct=yyy; buvid3=zzz; buvid_fp=www"
    从浏览器 DevTools → Application → Cookies → bilibili.com 复制。
    """
    if hasattr(_get_bilibili_cookies, "_cache"):
        return _get_bilibili_cookies._cache

    cookie_dict: dict[str, str] = {}
    raw_cookie = os.environ.get("BILIBILI_COOKIE", "")
    if raw_cookie:
        cookie_dict = _parse_cookie_string(raw_cookie)
        if cookie_dict.get("SESSDATA"):
            logger.info(f"[Bilibili] Loaded cookies from BILIBILI_COOKIE env ({len(cookie_dict)} keys)")
        elif cookie_dict:
            logger.warning("[Bilibili] BILIBILI_COOKIE missing SESSDATA — API may be rate-limited")
    else:
        logger.debug("[Bilibili] No BILIBILI_COOKIE env — API calls may be rate-limited")

    _get_bilibili_cookies._cache = cookie_dict
    return cookie_dict


def fetch_bilibili_space(uid: str, since: Optional[datetime] = None,
                         max_results: int = 20) -> list[FetchedItem]:
    """通过 B 站 wbi 签名 API 获取 UP 主最新投稿。"""
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping Bilibili fetch")
        return []

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://space.bilibili.com/",
    }
    cookies = _get_bilibili_cookies()

    logger.info(f"[Bilibili] Fetching uid={uid}")
    items: list[FetchedItem] = []
    try:
        params = _bili_sign_wbi({
            "mid": uid,
            "ps": max_results,
            "pn": 1,
            "order": "pubdate",
            "dm_img_list": "[]",
            "dm_img_str": "V2ViR0wgMS",
            "dm_cover_img_str": "QU5HTEUgKE",
        })
        resp = httpx.get(
            "https://api.bilibili.com/x/space/wbi/arc/search",
            params=params, headers=headers, cookies=cookies, timeout=15,
        )
        data = resp.json()
        code = data.get("code", -1)
        if code != 0:
            logger.warning(f"[Bilibili] API error code={code}: {data.get('message', '')}")
            return []

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

def _parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse 'key1=val1; key2=val2' into dict."""
    d: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            d[k.strip()] = v.strip()
    return d


def _get_weibo_cookies() -> dict[str, str]:
    """从环境变量 WEIBO_COOKIE 获取微博登录 cookie。

    格式：WEIBO_COOKIE="SUB=xxx; SUBP=yyy; XSRF-TOKEN=zzz"
    从浏览器 DevTools → Application → Cookies → weibo.com 复制完整 cookie 字符串。
    """
    if hasattr(_get_weibo_cookies, "_cache"):
        return _get_weibo_cookies._cache

    cookie_dict: dict[str, str] = {}
    raw_cookie = os.environ.get("WEIBO_COOKIE", "")
    if raw_cookie:
        cookie_dict = _parse_cookie_string(raw_cookie)
        if cookie_dict.get("SUB") and cookie_dict.get("XSRF-TOKEN"):
            logger.info(f"[Weibo] Loaded login cookies from WEIBO_COOKIE env ({len(cookie_dict)} keys)")
        elif cookie_dict:
            logger.warning("[Weibo] WEIBO_COOKIE missing SUB or XSRF-TOKEN — may fail auth")
        else:
            logger.warning("[Weibo] WEIBO_COOKIE env is set but could not parse any cookies")
    else:
        logger.warning("[Weibo] Set WEIBO_COOKIE env to enable Weibo monitoring")

    _get_weibo_cookies._cache = cookie_dict
    return cookie_dict


def fetch_nifd_research(page_url: str, since: Optional[datetime] = None,
                        max_results: int = 12) -> list[FetchedItem]:
    """从 NIFD 研究列表页抓取文章链接（直接解析 SeriesReportList API 端点）。"""
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping NIFD fetch")
        return []

    api_url = "http://www.nifd.cn/SeriesReport/SeriesReportList?pageSize=12&pageIndex=1&type=RC"
    logger.info(f"[NIFD] Fetching {api_url}")
    try:
        resp = httpx.get(api_url, follow_redirects=True, timeout=20,
                         headers={"User-Agent": _GENERIC_UA})
    except Exception as exc:
        logger.warning(f"[NIFD] Fetch failed: {exc}")
        return []

    if resp.status_code >= 400:
        logger.warning(f"[NIFD] HTTP {resp.status_code}")
        return []

    html = resp.text or ""
    # 解析 <a href="/SeriesReport/Details/4873">标题</a> + [2026年03月09日]
    _item_re = re.compile(
        r'href="(/SeriesReport/Details/\d+)"[^>]*>([^<]+)</a>'
        r'.*?\[(\d{4})年(\d{2})月(\d{2})日\]',
        re.DOTALL,
    )
    items: list[FetchedItem] = []
    for m in _item_re.finditer(html):
        path, title, y, mo, d = m.group(1), m.group(2).strip(), m.group(3), m.group(4), m.group(5)
        pub_at = datetime(int(y), int(mo), int(d), tzinfo=timezone.utc)
        if since and pub_at < since:
            continue
        full_url = f"http://www.nifd.cn{path}"
        items.append(FetchedItem(
            url=full_url,
            title=title,
            published_at=pub_at,
            raw_id=path,
        ))
    logger.info(f"[NIFD] Found {len(items)} items")
    return items[:max_results]


def fetch_weibo_user(profile_url: str, since: Optional[datetime] = None,
                     max_results: int = 10) -> list[FetchedItem]:
    """
    从微博用户主页抓取最新微博 URL。
    自动从浏览器提取登录 cookie（需先在 Chrome/Safari 登录微博）。
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; skipping Weibo fetch")
        return []

    # 从 profile URL 提取 uid
    uid_match = re.search(r"weibo\.com/(?:u/)?(\d+)(?:/|$)", profile_url)
    if not uid_match:
        logger.warning(f"[Weibo] Cannot extract uid from {profile_url}")
        return []
    uid = uid_match.group(1)

    cookies = _get_weibo_cookies()
    if not cookies:
        return []

    api_url = f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://weibo.com/{uid}",
        "Accept": "application/json, text/plain, */*",
    }
    # Weibo AJAX API 要求 X-XSRF-TOKEN header
    xsrf = cookies.get("XSRF-TOKEN", "")
    if xsrf:
        headers["X-XSRF-TOKEN"] = xsrf

    logger.info(f"[Weibo] Fetching uid={uid}")
    items: list[FetchedItem] = []
    try:
        resp = httpx.get(api_url, headers=headers, cookies=cookies, timeout=15)
        try:
            data = resp.json()
        except Exception:
            logger.warning(f"[Weibo] Non-JSON response (HTTP {resp.status_code})")
            return []

        if data.get("ok") != 1:
            msg = data.get("message", "")
            logger.warning(f"[Weibo] API error (ok={data.get('ok')}): {msg}")
            if "登录" in msg or resp.status_code == 403:
                logger.warning("[Weibo] Cookie 无效或过期 — 请在 Chrome 中打开 weibo.com 并登录")
                _get_weibo_cookies._cache = {}
            return []

        statuses = data.get("data", {}).get("list", [])
        for st in statuses[:max_results]:
            mid = st.get("id") or st.get("mid", "")
            if not mid:
                continue
            bid = st.get("bid") or mid
            post_url = f"https://weibo.com/{uid}/{bid}"
            # 优先用纯文本，去 HTML 标签
            raw_text = st.get("text_raw") or st.get("text") or ""
            title = re.sub(r"<[^>]+>", "", raw_text)[:80]
            created_at_str = st.get("created_at", "")
            pub_dt = _parse_dt(created_at_str) if created_at_str else None

            if since and pub_dt and _as_utc(pub_dt) <= _as_utc(since):
                continue

            items.append(FetchedItem(url=post_url, title=title, published_at=pub_dt, raw_id=str(mid)))
            time.sleep(0.3)
    except Exception as e:
        logger.error(f"[Weibo] Error fetching uid={uid}: {e}")

    logger.info(f"[Weibo] {len(items)} new items for uid={uid}")
    return items


# ── Twitter/X（通过 RSSHub 或 TwitterAPI.io）────────────────────────────────

def fetch_twitter_user(profile_url: str, since: Optional[datetime] = None,
                       max_results: int = 20) -> list[FetchedItem]:
    """
    从 Twitter/X 用户主页抓取最新推文。

    支持两种方式（按优先级）：
      1. RSSHUB_URL 环境变量 — 自部署 RSSHub 实例，走 /twitter/user/:id
      2. TWITTER_API_KEY 环境变量 — TwitterAPI.io 等第三方 API

    配置示例：
      RSSHUB_URL=https://rsshub.example.com
      # 或
      TWITTER_API_KEY=your-api-key
      TWITTER_API_BASE=https://api.twitterapi.io  （可选，默认 TwitterAPI.io）
    """
    # 提取用户名
    m = re.search(r"(?:twitter|x)\.com/(\w+)(?:/|$)", profile_url)
    if not m:
        logger.warning(f"[Twitter] Cannot extract username from {profile_url}")
        return []
    username = m.group(1)

    # 方式 1：RSSHub
    rsshub_url = os.environ.get("RSSHUB_URL", "").rstrip("/")
    if rsshub_url:
        rss_url = f"{rsshub_url}/twitter/user/{username}"
        logger.info(f"[Twitter] Fetching via RSSHub: {rss_url}")
        items = fetch_rss(rss_url, since=since)
        if items:
            logger.info(f"[Twitter] {len(items)} items via RSSHub for @{username}")
            return items[:max_results]
        logger.warning(f"[Twitter] RSSHub returned no items for @{username}")

    # 方式 2：TwitterAPI.io 兼容 API
    api_key = os.environ.get("TWITTER_API_KEY", "")
    if api_key:
        return _fetch_twitter_via_api(username, api_key, since, max_results)

    logger.info(
        f"[Twitter] No RSSHUB_URL or TWITTER_API_KEY configured; "
        f"skip @{username}. See docs for setup."
    )
    return []


def _fetch_twitter_via_api(
    username: str, api_key: str,
    since: Optional[datetime] = None, max_results: int = 20,
) -> list[FetchedItem]:
    """通过 TwitterAPI.io 兼容接口获取用户推文。"""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; skipping Twitter fetch")
        return []

    base = os.environ.get("TWITTER_API_BASE", "https://api.twitterapi.io").rstrip("/")
    url = f"{base}/twitter/user/last_tweets/{username}"
    headers = {"X-API-Key": api_key}

    logger.info(f"[Twitter] Fetching @{username} via API: {url}")
    items: list[FetchedItem] = []
    try:
        resp = httpx.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"[Twitter] API HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        tweets = data.get("tweets", data.get("data", []))
        if not isinstance(tweets, list):
            logger.warning(f"[Twitter] Unexpected API response shape")
            return []

        for tw in tweets[:max_results]:
            tweet_id = tw.get("id", "")
            text = tw.get("text", "")
            author = tw.get("author", {})
            screen_name = author.get("userName", username) if isinstance(author, dict) else username
            tweet_url = f"https://x.com/{screen_name}/status/{tweet_id}"
            title = re.sub(r"\s+", " ", text)[:120]

            pub_dt: Optional[datetime] = None
            created_str = tw.get("createdAt") or tw.get("created_at", "")
            if created_str:
                pub_dt = _parse_dt(created_str)
            if since and pub_dt and _as_utc(pub_dt) <= _as_utc(since):
                continue

            items.append(FetchedItem(
                url=tweet_url, title=title, published_at=pub_dt,
                raw_id=str(tweet_id),
            ))
    except Exception as e:
        logger.error(f"[Twitter] API error for @{username}: {e}")

    logger.info(f"[Twitter] {len(items)} items via API for @{username}")
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
    统一入口：根据 platform_hint（来自 sources.yaml）选择抓取方式。

    platform_hint 可能为：
      "substack", "rss", "youtube", "bilibili", "weibo",
      "linkedin", "twitter", "generic"
      以及任意自定义字符串（模糊匹配）
    """
    hint = (platform_hint or "").lower()

    # ── 子平台精确路由 ────────────────────────────────────────────────────────
    if hint in ("substack",):
        rss_url = substack_rss_url(url)
        items = fetch_rss(rss_url, since=since)
        return items if items else fetch_generic_links(url, since=since)

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

    if hint in ("twitter", "x"):
        return fetch_twitter_user(url, since=since)

    if hint in ("linkedin",):
        logger.info(f"[Monitor] Platform '{hint}' does not support auto-fetch; skip")
        return []

    # ── RSS 提示或通用域名匹配 ────────────────────────────────────────────────
    if hint in ("rss", "atom", "feed"):
        items = fetch_rss(url, since=since)
        return items if items else fetch_generic_links(url, since=since)

    # ── NIFD 专用路由 ────────────────────────────────────────────────────────
    if "nifd.cn" in url:
        return fetch_nifd_research(url, since=since)

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
            items = fetch_rss(url, since=since)
            return items if items else fetch_generic_links(url, since=since)

    # 最终兜底：先尝试 RSS，再尝试通用 HTML 列表页
    logger.info(f"[Monitor] Generic fetch attempt for {url}")
    items = fetch_rss(url, since=since)
    return items if items else fetch_generic_links(url, since=since)
