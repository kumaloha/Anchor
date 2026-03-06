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


class WebCollector(BaseCollector):
    """通用网页文章采集器，通过 Jina Reader 提取正文。"""

    @property
    def source_name(self) -> str:
        return "web"

    async def collect_by_url(self, url: str) -> RawPostData | None:
        text = await _fetch_jina(url)
        if not text:
            return None
        return _parse_article(text, url)

    async def collect(self, **kwargs) -> list[RawPostData]:
        if url := kwargs.get("url"):
            post = await self.collect_by_url(url)
            return [post] if post else []
        return []


# ---------------------------------------------------------------------------
# Jina 抓取
# ---------------------------------------------------------------------------


async def _fetch_jina(url: str) -> str | None:
    jina_url = _JINA_BASE + url
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            "-H", "Accept: text/plain",
            "--max-time", "30",
            jina_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=35)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text or "403: Forbidden" in text or "CAPTCHA" in text:
            logger.warning(f"[WebCollector] Jina blocked for {url}")
            return None
        return text
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
    posted_at = datetime.utcnow()
    for pattern in [
        r"Published Time:\s*(.+)",
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})",
        r"(\d{4}年\d{1,2}月\d{1,2}日\s*\d{2}:\d{2})",
    ]:
        if m := re.search(pattern, text):
            posted_at = _parse_time(m.group(1).strip())
            break

    # ── 作者/来源 ─────────────────────────────────────────────────────────
    author_name = _extract_author(text, url)

    # ── 正文（Markdown Content 块之后）────────────────────────────────────
    if m := re.search(r"Markdown Content:\s*\n(.*)", text, re.DOTALL):
        content_raw = m.group(1).strip()
    else:
        content_raw = text

    content = _clean_content(content_raw, title)

    # ── 提取图片 URL（从 Jina markdown 的 ![...](url) 语法）────────────────
    media_items = _extract_images(content_raw)

    # ── 外部 ID：URL hash ─────────────────────────────────────────────────
    import hashlib
    external_id = hashlib.md5(url.encode()).hexdigest()[:16]

    return RawPostData(
        source="web",
        external_id=external_id,
        content=content or title,
        author_name=author_name,
        author_id=author_name,
        url=url,
        posted_at=posted_at,
        metadata={"title": title, "source_url": url},
        media_items=media_items,
    )


def _extract_author(text: str, url: str) -> str:
    """从文章文本或 URL 提取作者/来源机构。"""
    # 优先匹配"来源：XXX"
    for pattern in [
        r"来源[：:]\s*([^\n\r，,]+)",
        r"作者[：:]\s*([^\n\r，,]+)",
        r"By\s+([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})",
        r"Source[：:]\s*([^\n\r]+)",
    ]:
        if m := re.search(pattern, text):
            name = m.group(1).strip()
            # 去除多余尾缀（如 " 分享到：" 等）
            name = re.split(r"\s{2,}|分享|【", name)[0].strip()
            if name:
                return name
    # 降级：使用域名
    domain = urlparse(url).netloc
    return domain.replace("www.", "").split(".")[0]


def _extract_images(raw: str) -> list[dict]:
    """从 Jina markdown 中提取图片 URL，过滤掉图标/二维码等无意义小图。"""
    urls = re.findall(r"!\[.*?\]\((https?://[^\s)]+)\)", raw)
    seen: set[str] = set()
    items: list[dict] = []
    skip_keywords = ("icon", "logo", "banner", "qrcode", "zxcode", "space.gif", "favicon")
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
    for url in urls:
        url_lower = url.lower()
        if any(kw in url_lower for kw in skip_keywords):
            continue
        # 过滤掉非图片扩展名（如 .html）
        if not any(url_lower.split("?")[0].endswith(ext) for ext in image_exts):
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"type": "photo", "url": url})
    return items


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
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
