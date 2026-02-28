"""
Layer 1 — Step 1: 输入处理器
=============================
接收用户输入的 URL（单条帖子或作者主页），执行以下操作：
  1. 解析平台和 URL 类型（帖子 / 主页）
  2. 通过平台 API 抓取内容
  3. 写入 raw_posts 和 authors
  4. 注册 MonitoredSource（后续由调度器持续追踪）

公共入口：process_url(url, session)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.collector.base import RawPostData
from anchor.models import Author, MonitoredSource, RawPost, SourceType, _utcnow


# ---------------------------------------------------------------------------
# URL 解析
# ---------------------------------------------------------------------------


@dataclass
class ParsedURL:
    platform: str        # "twitter" | "weibo"
    source_type: SourceType
    platform_id: str     # 帖子 ID 或用户 ID / 用户名
    canonical_url: str


_TWITTER_POST = re.compile(
    r"(?:twitter\.com|x\.com)/\w+/status/(\d+)"
)
_TWITTER_PROFILE = re.compile(
    r"(?:twitter\.com|x\.com)/(@?[\w]+)/?$"
)
_WEIBO_POST = re.compile(
    r"weibo\.com/\d+/(\w+)|m\.weibo\.cn/(?:status|detail)/(\w+)"
)
_WEIBO_PROFILE = re.compile(
    r"weibo\.com/(?:u/)?(\d+)/?$|weibo\.com/([\w]+)/?$"
)
_YOUTUBE_VIDEO = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})"
)


def parse_url(url: str) -> ParsedURL:
    """解析输入 URL，返回平台、类型、平台 ID。"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    host = urlparse(url).netloc.lower()

    # --- Twitter / X ---
    if "twitter.com" in host or "x.com" in host:
        if m := _TWITTER_POST.search(url):
            return ParsedURL("twitter", SourceType.POST, m.group(1), url)
        if m := _TWITTER_PROFILE.search(url):
            username = m.group(1).lstrip("@")
            return ParsedURL(
                "twitter", SourceType.PROFILE, username,
                f"https://twitter.com/{username}"
            )

    # --- 微博 ---
    if "weibo.com" in host or "m.weibo.cn" in host:
        if m := _WEIBO_POST.search(url):
            post_id = m.group(1) or m.group(2)
            return ParsedURL("weibo", SourceType.POST, post_id, url)
        if m := _WEIBO_PROFILE.search(url):
            user_id = m.group(1) or m.group(2)
            return ParsedURL(
                "weibo", SourceType.PROFILE, user_id,
                f"https://weibo.com/{user_id}"
            )

    # --- YouTube ---
    if "youtube.com" in host or "youtu.be" in host:
        if m := _YOUTUBE_VIDEO.search(url):
            video_id = m.group(1)
            return ParsedURL(
                "youtube", SourceType.POST, video_id,
                f"https://www.youtube.com/watch?v={video_id}"
            )

    raise ValueError(f"无法识别的 URL 或不支持的平台：{url}")


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------


@dataclass
class InputResult:
    monitored_source: MonitoredSource
    author: Author
    raw_posts: list[RawPost]          # 本次新入库的帖子
    is_new_source: bool               # 是否为首次添加该监控源


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def process_url(url: str, session: AsyncSession) -> InputResult:
    """处理用户输入的单个 URL。

    - 帖子 URL：抓取该帖子，注册监控（后续追踪引用/回复更新）
    - 主页 URL：抓取近 1 年内容，注册监控（后续追踪新帖子）
    """
    parsed = parse_url(url)
    logger.info(f"Processing URL: platform={parsed.platform}, type={parsed.source_type}, id={parsed.platform_id}")

    # 检查是否已存在相同监控源
    existing = await session.exec(
        select(MonitoredSource).where(
            MonitoredSource.platform == parsed.platform,
            MonitoredSource.platform_id == parsed.platform_id,
            MonitoredSource.source_type == parsed.source_type,
        )
    )
    existing_source = existing.first()

    if existing_source:
        logger.info(f"MonitoredSource already exists (id={existing_source.id}), skipping registration")
        author = await session.get(Author, existing_source.author_id)
        return InputResult(
            monitored_source=existing_source,
            author=author,
            raw_posts=[],
            is_new_source=False,
        )

    # 根据平台获取采集器
    fetcher = _get_fetcher(parsed.platform)

    # 抓取内容
    if parsed.source_type == SourceType.POST:
        raw_posts_data = await fetcher.fetch_post(parsed.platform_id)
    else:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
        raw_posts_data = await fetcher.fetch_profile(parsed.platform_id, since=since)

    # 获取或创建 Author
    author = await _get_or_create_author(session, parsed, raw_posts_data)

    # 注册 MonitoredSource
    source = MonitoredSource(
        url=parsed.canonical_url,
        source_type=parsed.source_type,
        platform=parsed.platform,
        platform_id=parsed.platform_id,
        author_id=author.id,
        is_active=True,
        history_fetched=(parsed.source_type == SourceType.PROFILE),
    )
    session.add(source)
    await session.flush()  # 获取 source.id

    # 写入 raw_posts（去重）
    saved_posts = await _save_raw_posts(session, raw_posts_data, source.id)
    await session.commit()

    logger.info(
        f"Registered MonitoredSource id={source.id}, "
        f"saved {len(saved_posts)} new posts"
    )
    return InputResult(
        monitored_source=source,
        author=author,
        raw_posts=saved_posts,
        is_new_source=True,
    )


# ---------------------------------------------------------------------------
# 持续追踪：调度器调用此函数轮询已注册的监控源
# ---------------------------------------------------------------------------


async def poll_monitored_source(
    source: MonitoredSource, session: AsyncSession
) -> list[RawPost]:
    """轮询一个监控源，写入新内容，更新 last_fetched_at。"""
    fetcher = _get_fetcher(source.platform)

    since = source.last_fetched_at
    if source.source_type == SourceType.POST:
        raw_posts_data = await fetcher.fetch_post_updates(
            source.platform_id, since=since
        )
    else:
        raw_posts_data = await fetcher.fetch_profile_since(
            source.platform_id, since=since
        )

    saved = await _save_raw_posts(session, raw_posts_data, source.id)
    source.last_fetched_at = _utcnow()
    session.add(source)
    await session.commit()
    return saved


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _get_fetcher(platform: str):
    """返回对应平台的采集器实例（懒加载）"""
    if platform == "twitter":
        from anchor.collector.twitter import TwitterCollector
        return _TwitterFetchAdapter(TwitterCollector())
    if platform == "weibo":
        from anchor.collector.weibo import WeiboCollector
        return _WeiboFetchAdapter(WeiboCollector())
    if platform == "youtube":
        from anchor.collector.youtube import YouTubeCollector
        return _YouTubeFetchAdapter(YouTubeCollector())
    raise ValueError(f"不支持的平台：{platform}")


async def _get_or_create_author(
    session: AsyncSession,
    parsed: ParsedURL,
    posts: list[RawPostData],
) -> Author:
    """从已抓取的帖子中提取作者信息，写入或复用 authors 表。"""
    author_name = posts[0].author_name if posts else parsed.platform_id
    author_platform_id = posts[0].author_id if posts else parsed.platform_id

    existing = await session.exec(
        select(Author).where(
            Author.platform == parsed.platform,
            Author.platform_id == author_platform_id,
        )
    )
    author = existing.first()
    if author:
        return author

    author = Author(
        name=author_name,
        platform=parsed.platform,
        platform_id=author_platform_id,
        profile_url=f"https://{parsed.platform}.com/{author_platform_id}",
    )
    session.add(author)
    await session.flush()
    return author


async def _save_raw_posts(
    session: AsyncSession,
    posts: list[RawPostData],
    monitored_source_id: int,
) -> list[RawPost]:
    """批量写入 raw_posts，跳过已存在的（按 source + external_id 去重）。"""
    import json

    saved: list[RawPost] = []
    for p in posts:
        existing = await session.exec(
            select(RawPost).where(
                RawPost.source == p.source,
                RawPost.external_id == p.external_id,
            )
        )
        if existing.first():
            continue

        db_post = RawPost(
            source=p.source,
            external_id=p.external_id,
            content=p.content,
            author_name=p.author_name,
            author_platform_id=p.author_id,
            url=p.url,
            posted_at=p.posted_at,
            raw_metadata=json.dumps(p.metadata, ensure_ascii=False),
            media_json=json.dumps(p.media_items, ensure_ascii=False) if p.media_items else None,
            monitored_source_id=monitored_source_id,
        )
        session.add(db_post)
        saved.append(db_post)

    await session.flush()
    return saved


# ---------------------------------------------------------------------------
# 平台采集器适配层
# （统一 fetch_post / fetch_profile 接口，屏蔽各采集器的差异）
# ---------------------------------------------------------------------------


class _TwitterFetchAdapter:
    def __init__(self, collector) -> None:
        self._c = collector

    async def fetch_post(self, tweet_id: str) -> list[RawPostData]:
        return await self._c.collect_by_ids([tweet_id])

    async def fetch_post_updates(
        self, tweet_id: str, since: datetime | None
    ) -> list[RawPostData]:
        # 追踪引用和回复（conversation_id）
        return await self._c.collect_conversation(tweet_id, since=since)

    async def fetch_profile(
        self, username: str, since: datetime
    ) -> list[RawPostData]:
        return await self._c.collect_user_timeline(username, since=since)

    async def fetch_profile_since(
        self, username: str, since: datetime | None
    ) -> list[RawPostData]:
        return await self._c.collect_user_timeline(username, since=since)


class _WeiboFetchAdapter:
    def __init__(self, collector) -> None:
        self._c = collector

    async def fetch_post(self, post_id: str) -> list[RawPostData]:
        return await self._c.collect_by_ids([post_id])

    async def fetch_post_updates(
        self, post_id: str, since: datetime | None
    ) -> list[RawPostData]:
        # 微博评论追踪暂未实现，返回空列表
        return []

    async def fetch_profile(
        self, uid: str, since: datetime
    ) -> list[RawPostData]:
        return await self._c.collect(uids=[uid])

    async def fetch_profile_since(
        self, uid: str, since: datetime | None
    ) -> list[RawPostData]:
        return await self._c.collect(uids=[uid])


class _YouTubeFetchAdapter:
    def __init__(self, collector) -> None:
        self._c = collector

    async def fetch_post(self, video_id: str) -> list[RawPostData]:
        return await self._c.collect_by_ids([video_id])

    async def fetch_post_updates(
        self, video_id: str, since: datetime | None
    ) -> list[RawPostData]:
        # YouTube 视频内容不变，返回空列表
        return []

    async def fetch_profile(
        self, channel_id: str, since: datetime
    ) -> list[RawPostData]:
        # 频道主页抓取暂未实现
        return []

    async def fetch_profile_since(
        self, channel_id: str, since: datetime | None
    ) -> list[RawPostData]:
        return []
