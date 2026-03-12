"""通用网页文章采集器

通过 Jina Reader 提取任意公开网页的文章内容（无需认证）。
适用于新闻网站、博客、政府公告等无专属 API 的来源。

采集结果：
  source     = "web"
  author_name = 从 "来源：XXX" / "作者：XXX" / 域名 推断
  content    = 正文 markdown（去除导航、页脚等噪声）
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from urllib.parse import urlparse

from loguru import logger

from anchor.collect.base import BaseCollector, RawPostData

_JINA_BASE = "https://r.jina.ai/"

# arXiv → ar5iv 重定向（ar5iv 提供论文 HTML 渲染，Jina Reader 解析效果远优于 PDF）
_ARXIV_RE = re.compile(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)")


def _arxiv_to_ar5iv(url: str) -> str:
    m = _ARXIV_RE.match(url)
    if m:
        return f"https://ar5iv.labs.arxiv.org/html/{m.group(1)}"
    return url


class WebCollector(BaseCollector):
    """通用网页文章采集器，通过 Jina Reader 提取正文。"""

    @property
    def source_name(self) -> str:
        return "web"

    async def collect_by_url(self, url: str) -> RawPostData | None:
        fetch_url = _arxiv_to_ar5iv(url)
        if fetch_url != url:
            logger.info(f"[WebCollector] arXiv → ar5iv: {fetch_url}")
        text = await _fetch_jina(fetch_url)
        if not text:
            return None
        post = _parse_article(text, url)
        # Jina 未检测到 YouTube embed → 回退抓原始 HTML 提取 YouTube ID
        # 无条件尝试：Jina 返回的导航文本长度不稳定，is_video_only 判断不可靠
        meta = post.metadata or {}
        if not meta.get("youtube_redirect"):
            yt_id = await _extract_youtube_id_from_raw_html(url)
            if yt_id:
                meta["youtube_redirect"] = f"https://www.youtube.com/watch?v={yt_id}"
                post.metadata = meta
                logger.info(f"[WebCollector] YouTube ID found from raw HTML: {yt_id} for {url}")
        return post

    async def collect(self, **kwargs) -> list[RawPostData]:
        if url := kwargs.get("url"):
            post = await self.collect_by_url(url)
            return [post] if post else []
        return []


# ---------------------------------------------------------------------------
# Jina 抓取
# ---------------------------------------------------------------------------


async def _fetch_jina(url: str) -> str | None:
    # PDF 和大型文档需要更长超时
    is_pdf = url.lower().endswith(".pdf") or "/pdf/" in url.lower()
    max_time = "180" if is_pdf else "60"
    wait_timeout = 200 if is_pdf else 70

    jina_url = _JINA_BASE + url
    logger.info(f"[WebCollector] Fetching via Jina (max_time={max_time}s): {url}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            "-H", "Accept: text/plain",
            "--max-time", max_time,
            jina_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=wait_timeout)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or "403: Forbidden" in text or "CAPTCHA" in text:
            logger.warning(f"[WebCollector] Jina blocked for {url}")
            return None
        logger.info(f"[WebCollector] Jina returned {len(text)} chars for {url}")
        return text
    except asyncio.TimeoutError:
        logger.error(f"[WebCollector] Jina timeout ({max_time}s) for {url}")
        return None
    except Exception as exc:
        logger.error(f"[WebCollector] fetch failed for {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# 文章解析
# ---------------------------------------------------------------------------


def _parse_article(text: str, url: str) -> RawPostData:
    """从 Jina markdown 中解析文章元数据和正文。"""

    # ── 标题 ──────────────────────────────────────────────────────────────
    title = ""
    if m := re.search(r"^Title:\s*(.+)$", text, re.MULTILINE):
        title = m.group(1).strip()

    # ── 发布时间 ──────────────────────────────────────────────────────────
    now = datetime.utcnow()
    posted_at = now
    for pattern in [
        r"Published Time:\s*(.+)",
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日\s*\d{2}:\d{2})",
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})",
    ]:
        if m := re.search(pattern, text):
            posted_at = _parse_time(m.group(1).strip())
            break

    # 兜底：若日期未成功提取（仍为当前时间），尝试从 URL 路径提取年份
    if abs((posted_at - now).total_seconds()) < 60:
        url_year = _extract_year_from_url(url)
        if url_year:
            posted_at = datetime(url_year, 1, 1)

    # ── 作者/来源 ─────────────────────────────────────────────────────────
    author_name = _extract_author(text, url)

    # ── 正文（Markdown Content 块之后）────────────────────────────────────
    if m := re.search(r"Markdown Content:\s*\n(.*)", text, re.DOTALL):
        content_raw = m.group(1).strip()
    else:
        content_raw = text

    # ── YouTube 嵌入检测 ──────────────────────────────────────────────────
    youtube_video_id = _extract_youtube_id(content_raw)
    youtube_redirect = (
        f"https://www.youtube.com/watch?v={youtube_video_id}"
        if youtube_video_id else None
    )

    # ── 视频/轻内容页检测 ─────────────────────────────────────────────────
    # 1) 纯播放器 UI（DoubleLine 风格：视频控件关键词 ≥2 个）
    # 2) 内容包装页：嵌入了非 YouTube 视频且有效正文 < 400 字
    # 3) 一般内容过短：有效正文 < 200 字
    meaningful_len = _meaningful_text_length(content_raw)
    has_non_yt_video = _has_embedded_video(content_raw)
    is_video_only = (
        _is_video_only_page(content_raw)
        or (has_non_yt_video and meaningful_len < 800)   # 嵌入非 YT 视频 + 正文稀少
        or meaningful_len < 200                           # 一般页面正文极少
    )

    if is_video_only:
        logger.info(
            f"[WebCollector] Video/wrapper page (meaningful={meaningful_len} chars)"
            + (f", YouTube embed: {youtube_redirect}" if youtube_redirect else "")
            + f": {url}"
        )
        content = ""
    else:
        content = _clean_content(content_raw, title)

    # ── 提取图片 URL（从 Jina markdown 的 ![...](url) 语法）────────────────
    media_items = _extract_images(content_raw, source_url=url)

    # ── 外部 ID：URL hash ─────────────────────────────────────────────────
    import hashlib
    external_id = hashlib.md5(url.encode()).hexdigest()[:16]

    return RawPostData(
        source="web",
        external_id=external_id,
        content=content or title,   # title 作保底（至少有字）
        author_name=author_name,
        author_id=author_name,
        url=url,
        posted_at=posted_at,
        metadata={
            "title": title,
            "source_url": url,
            "is_video_only": is_video_only,
            "youtube_redirect": youtube_redirect,   # 非 None → 管道应改抓此 URL
        },
        media_items=media_items,
    )


# 域名 → 规范机构名（当文章未标明作者时用域名推断机构）
_DOMAIN_TO_INSTITUTION: dict[str, str] = {
    "goldmansachs":   "Goldman Sachs",
    "morganstanley":  "Morgan Stanley",
    "blackrock":      "BlackRock",
    "blackstone":     "Blackstone",
    "bridgewater":    "Bridgewater Associates",
    "imf":            "IMF",
    "federalreserve": "Federal Reserve",
    "ecb":            "ECB",
    "bankofengland":  "Bank of England",
    "boj":            "Bank of Japan",
    "pbc":            "People's Bank of China",
    "bis":            "BIS",
    "oaktreecapital": "Oaktree Capital",
    "doubleline":     "DoubleLine Capital",
    "citadel":        "Citadel",
    "piie":           "PIIE",
    "brookings":      "Brookings Institution",
    "nber":           "NBER",
    "hoover":         "Hoover Institution",
    "apolloacademy":  "Apollo Global Management",
    "nri":            "Nomura Research Institute",
    "rieti":          "RIETI",
}


def _extract_author(text: str, url: str) -> str:
    """从文章文本或 URL 提取作者/来源机构。

    优先级：
      1. 中文「作者/来源」字段
      2. 英文「By/Author/Written by/Published by」（含机构名，不限于人名）
      3. 「Source:」字段
      4. 域名 → 规范机构名映射
      5. 域名第一段（兜底）
    """
    for pattern in [
        r"作者[：:]\s*([^\n\r，,]{2,40})",
        r"来源[：:]\s*([^\n\r，,]{2,40})",
        # 英文作者/机构：支持人名和机构名（1-6 个首字母大写的词）
        r"(?:By|Authors?|Written by|Published by)[：:\s]+"
        r"((?:[A-Z][A-Za-z0-9&'.,-]+)(?:\s+(?:[A-Z][A-Za-z0-9&'.,-]+)){0,5})",
        r"Source[：:]\s*([^\n\r]{2,60})",
    ]:
        if m := re.search(pattern, text):
            name = m.group(1).strip()
            name = re.split(r"\s{2,}|分享|【|\|", name)[0].strip()
            if name and not name.startswith(("http://", "https://", "www.")):
                return name

    # 域名 → 规范机构名
    domain = urlparse(url).netloc.lower().replace("www.", "")
    domain_key = domain.split(".")[0]
    return _DOMAIN_TO_INSTITUTION.get(domain_key, domain_key)


def _extract_images(raw: str, source_url: str = "") -> list[dict]:
    """从 Jina markdown 中提取图片 URL，过滤掉图标/二维码/同源受限图片等。"""
    from urllib.parse import urlparse as _urlparse
    source_domain = _urlparse(source_url).netloc.lower().lstrip("www.") if source_url else ""

    urls = re.findall(r"!\[.*?\]\((https?://[^\s)]+)\)", raw)
    seen: set[str] = set()
    items: list[dict] = []
    skip_keywords = ("icon", "logo", "banner", "qrcode", "zxcode", "space.gif", "favicon")
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    # 已知需要认证、无法公开访问的研究/机构域名
    _GATED_DOMAINS = (
        "gspublishing.com", "gs.com",
        "morganstanley.com", "msci.com",
        "blackstone.com", "oaktreecapital.com",
        "doubleline.com", "bridgewater.com",
        "kkr.com", "apolloglobal.com",
    )

    for url in urls:
        url_lower = url.lower()
        img_domain = _urlparse(url).netloc.lower().lstrip("www.")

        # 过滤：图标/装饰类
        if any(kw in url_lower for kw in skip_keywords):
            continue
        # 过滤：非图片扩展名
        if not any(url_lower.split("?")[0].endswith(ext) for ext in image_exts):
            continue
        # 过滤：与文章同源（需要认证的图片）
        if source_domain and img_domain and (
            img_domain == source_domain or img_domain.endswith("." + source_domain)
        ):
            continue
        # 过滤：已知需要认证的机构域名
        if any(img_domain == d or img_domain.endswith("." + d) for d in _GATED_DOMAINS):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"type": "photo", "url": url})
    return items


# 视频播放器 UI 关键词（出现 ≥2 个即判定为纯视频播放器 UI 页）
_VIDEO_UI_KEYWORDS = [
    "Video Player is loading",
    "Playback Rate",
    "Play Video",
    "Picture-in-Picture",
    "Stream Type LIVE",
    "Beginning of dialog window",
]

# YouTube 视频 URL 正则（embed / watch / shorts / youtu.be）
_YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|"
    r"youtu\.be/"
    r")([\w\-]{11})"
)


def _extract_youtube_id(raw: str) -> str | None:
    """从 Jina 内容中提取嵌入的 YouTube 视频 ID（返回第一个匹配）。"""
    m = _YOUTUBE_URL_RE.search(raw)
    return m.group(1) if m else None


def _is_video_only_page(raw: str) -> bool:
    """判断 Jina 抓取的页面是否为纯视频播放器（无文字正文）。"""
    hits = sum(1 for kw in _VIDEO_UI_KEYWORDS if kw in raw)
    return hits >= 2


def _meaningful_text_length(content_raw: str) -> int:
    """
    估算页面中「真正文章正文」的字符数。
    策略：把行内所有 [text](url) 去掉后再计字符，同时跳过导航/表单/页脚行。
    只统计净文字 ≥ 60 字符的行（真正的段落），排除菜单项、标题链接等噪声。
    """
    _SKIP_RE = re.compile(
        r"^\s*[\*\-]\s+\[.*?\]\(.*?\)\s*$"      # 带链接的 bullet（纯导航）
        r"|^#{1,6}\s+\[.*?\]\(.*?\)\s*$"         # 标题链接（related articles）
        r"|^\[.*?\]\(.*?\)\s*$"                  # 纯链接行
        r"|Please fill out|Sign up for|Stay up-to-date"
        r"|Email Address|First Name|Last Name|Job Title"
        r"|Submit|Clear Results|Apply Filter|See Results"
        r"|Footnote Title|Institutional quality|press inquiries"
        r"|click here|For office|Individual investor",
        re.IGNORECASE,
    )
    _LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # 去掉 [text](url) 中的 url 部分

    total = 0
    for line in content_raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _SKIP_RE.search(stripped):
            continue
        # 把 Markdown 链接替换为纯文字再计长度
        text_only = _LINK_RE.sub(r"\1", stripped).strip()
        # 只计入净文字 ≥ 60 字符的行
        if len(text_only) >= 60:
            total += len(text_only)
    return total


def _has_embedded_video(content_raw: str) -> bool:
    """检测页面是否嵌入了非 YouTube 的视频（Brightcove、Vimeo 等）。"""
    return bool(re.search(r"brightcove\.net|vimeo\.com/video/|wistia\.com", content_raw, re.IGNORECASE))


def _clean_content(raw: str, title: str) -> str:
    """去除导航链接、图片、版权行等噪声，保留正文。"""
    lines = raw.splitlines()
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 跳过：纯图片行、纯链接行、空行（多个连续空行压缩为一个）、版权行
        if re.match(r"^!\[.*?\]\(.*?\)$", stripped):
            continue
        if re.match(r"^\[.*?\]\(.*?\)$", stripped):
            continue
        if re.search(r"Copyright|版权所有|制作单位|责任编辑", stripped):
            continue
        if stripped == title:
            continue
        clean.append(line)

    text = "\n".join(clean).strip()
    # 压缩连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _extract_year_from_url(url: str) -> int | None:
    """从 URL 路径中提取四位年份（1900-2099），用于归档类页面兜底。

    例如：/letters/1980.html → 1980, /report/2024/overview → 2024
    仅匹配合理年份范围，避免把端口号、ID 等误判为年份。
    """
    path = urlparse(url).path
    m = re.search(r"(?:^|/)(\d{4})(?:[/.]|$)", path)
    if m:
        year = int(m.group(1))
        if 1900 <= year <= 2099:
            return year
    return None


def _parse_time(raw: str) -> datetime:
    if not raw:
        return datetime.utcnow()
    # "2026年3月5日 10:34"
    if m := re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{2}):(\d{2})", raw):
        try:
            return datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)),
            )
        except Exception:
            pass
    for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            pass
    # "February 26, 1982" / "March 5, 2025"
    for fmt in ["%B %d, %Y", "%b %d, %Y"]:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except Exception:
            pass
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


# 从原始 HTML 提取 YouTube 视频 ID 的正则（支持 data-video-id 和 embed URL）
_RAW_HTML_YT_RE = re.compile(
    r'data-video-id=["\']([A-Za-z0-9_-]{11})["\']'
    r'|youtube\.com/embed/([A-Za-z0-9_-]{11})'
)


async def _extract_youtube_id_from_raw_html(url: str) -> str | None:
    """直接抓取原始 HTML，从 data-video-id 或 embed URL 中提取 YouTube 视频 ID。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-L",
            "-A", "Mozilla/5.0",
            "--max-time", "15",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        html = stdout.decode("utf-8", errors="replace")
        m = _RAW_HTML_YT_RE.search(html)
        if m:
            return m.group(1) or m.group(2)
    except Exception as exc:
        logger.debug(f"[WebCollector] raw HTML YouTube extraction failed: {exc}")
    return None
