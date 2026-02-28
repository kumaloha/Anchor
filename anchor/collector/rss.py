"""RSS 采集器

使用 feedparser 解析标准 RSS/Atom 源。
无需 API Key，直接抓取公开 RSS 源。

默认源列表见 anchor/config.py DEFAULT_RSS_FEEDS。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from anchor.collector.base import BaseCollector, RawPostData
from anchor.config import settings


class RSSCollector(BaseCollector):
    """从 RSS/Atom 源采集财经文章"""

    def __init__(self, feeds: list[str] | None = None) -> None:
        self._feeds = feeds or settings.rss_feed_list

    @property
    def source_name(self) -> str:
        return "rss"

    async def collect(self, feeds: list[str] | None = None, **_) -> list[RawPostData]:
        """遍历所有配置的 RSS 源，返回最新条目。

        Args:
            feeds: 覆盖默认源列表
        """
        feed_list = feeds or self._feeds
        posts: list[RawPostData] = []
        for url in feed_list:
            posts.extend(self._fetch_feed(url))
        return posts

    def _fetch_feed(self, url: str) -> list[RawPostData]:
        try:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "Anchor/1.0"})
        except Exception as exc:
            print(f"[RSSCollector] failed to fetch {url!r}: {exc}")
            return []

        posts: list[RawPostData] = []
        feed_title = parsed.feed.get("title", url)

        for entry in parsed.entries:
            content = _extract_content(entry)
            if not content:
                continue

            posted_at = _parse_time(entry)
            # 用 URL 或 id 字段生成稳定的 external_id
            link = entry.get("link", "")
            entry_id = entry.get("id", link) or link
            external_id = hashlib.sha1(entry_id.encode()).hexdigest()[:16]

            posts.append(
                RawPostData(
                    source=self.source_name,
                    external_id=external_id,
                    content=content,
                    author_name=_extract_author(entry, feed_title),
                    author_id=None,
                    url=link,
                    posted_at=posted_at,
                    metadata={"feed_title": feed_title, "feed_url": url},
                )
            )
        return posts


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _extract_content(entry: dict) -> str:
    """优先取正文，其次摘要，最后标题"""
    if entry.get("content"):
        return entry["content"][0].get("value", "").strip()
    if entry.get("summary"):
        return entry["summary"].strip()
    return entry.get("title", "").strip()


def _extract_author(entry: dict, feed_title: str) -> str:
    author = entry.get("author", "").strip()
    return author if author else feed_title


def _parse_time(entry: dict) -> datetime:
    """尝试多种方式解析发布时间，失败则返回 UTC 当前时间"""
    # feedparser 解析好的结构化时间
    if entry.get("published_parsed"):
        import time

        t = entry["published_parsed"]
        try:
            return datetime(*t[:6])
        except Exception:
            pass

    # 原始字符串
    for key in ("published", "updated", "created"):
        raw = entry.get(key, "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.replace(tzinfo=None) if dt.tzinfo else dt
            except Exception:
                pass

    return datetime.utcnow()
