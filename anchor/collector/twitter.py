"""Twitter/X 采集器

使用 Tweepy v4 的 Twitter API v2 接口。
需要在 .env 中配置 TWITTER_BEARER_TOKEN（批量搜索和时间线用此）。

未配置 Token 时，单条推文抓取回落到 Syndication API（无需认证）。
批量关键词搜索和用户时间线仍需 TWITTER_BEARER_TOKEN。

额外支持 X Article（长文）内容提取：将 title + preview_text 作为正文。
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone

import httpx

from anchor.collector.base import BaseCollector, RawPostData
from anchor.config import settings


_ECONOMIC_KEYWORDS = [
    "股市 预测 OR 看多 OR 看空",
    "A股 分析 OR 判断",
    "美股 预测 OR 趋势",
    "降息 OR 加息 分析",
    "经济衰退 OR 软着陆",
    "GDP 增长 预测",
    "通胀 OR 通货膨胀 趋势",
    "人民币 汇率 预测",
    "黄金 OR 原油 看多 OR 看空",
]

_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"


def _get_syndication_token(tweet_id: str) -> str:
    """计算 Syndication API 所需的 token。

    公式来自 Vercel react-tweet 逆向工程，经 yt-dlp 验证（2025）：
      token = ((id / 1e15) * π).toString(36).replace(/(0+|\\.)/g, '')

    直接传 token=0 在较新推文上已失效。
    """
    val = (int(tweet_id) / 1e15) * math.pi
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    integer = int(val)
    frac = val - integer
    parts: list[str] = []
    while integer > 0:
        parts.insert(0, chars[integer % 36])
        integer //= 36
    if not parts:
        parts = ["0"]
    if frac > 0:
        parts.append(".")
        for _ in range(10):
            frac *= 36
            d = int(frac)
            parts.append(chars[d])
            frac -= d
    return re.sub(r"(0+|\.)", "", "".join(parts))


class TwitterCollector(BaseCollector):
    """Twitter/X 采集器，支持 API 模式（需 token）和 Syndication 模式（无需 token）。"""

    def __init__(self, keywords: list[str] | None = None) -> None:
        self._use_api = bool(settings.twitter_bearer_token)
        if self._use_api:
            import tweepy
            self._client = tweepy.AsyncClient(
                bearer_token=settings.twitter_bearer_token,
                wait_on_rate_limit=True,
            )
        else:
            self._client = None
        self._keywords = keywords or _ECONOMIC_KEYWORDS

    @property
    def source_name(self) -> str:
        return "twitter"

    # ------------------------------------------------------------------
    # 公开接口（供 _TwitterFetchAdapter 调用）
    # ------------------------------------------------------------------

    async def collect_by_ids(self, tweet_ids: list[str]) -> list[RawPostData]:
        """按 ID 列表抓取推文。有 token 优先用官方 API，否则回落 Syndication API。"""
        if self._use_api:
            result = await self._collect_by_ids_api(tweet_ids)
            if result:
                return result
        return await self._collect_by_ids_syndication(tweet_ids)

    async def collect_user_timeline(
        self, username: str, since: datetime | None = None
    ) -> list[RawPostData]:
        """抓取用户时间线（需要 TWITTER_BEARER_TOKEN；无 token 返回空列表）。"""
        if not self._use_api:
            return []
        return await self._user_timeline_by_username(username, since)

    async def collect_conversation(
        self, tweet_id: str, since: datetime | None = None
    ) -> list[RawPostData]:
        """抓取会话/回复更新（需要 TWITTER_BEARER_TOKEN；无 token 返回空列表）。"""
        if not self._use_api:
            return []
        return await self._fetch_conversation(tweet_id, since)

    async def collect(
        self,
        keywords: list[str] | None = None,
        user_ids: list[str] | None = None,
        max_results: int | None = None,
    ) -> list[RawPostData]:
        """批量采集：关键词搜索 + 指定账号时间线（需要 TWITTER_BEARER_TOKEN）。"""
        if not self._use_api:
            return []

        max_results = max_results or min(settings.collector_max_results_per_query, 100)
        posts: list[RawPostData] = []

        for query in keywords or self._keywords:
            full_query = f"({query}) -is:retweet lang:zh"
            posts.extend(await self._search(full_query, max_results))

        if user_ids:
            for uid in user_ids:
                posts.extend(await self._user_timeline(uid, max_results))

        return posts

    # ------------------------------------------------------------------
    # 官方 API 实现
    # ------------------------------------------------------------------

    async def _collect_by_ids_api(self, tweet_ids: list[str]) -> list[RawPostData]:
        try:
            response = await self._client.get_tweets(
                ids=tweet_ids,
                tweet_fields=["created_at", "author_id", "public_metrics"],
                expansions=["author_id"],
                user_fields=["username", "name"],
            )
            return self._parse_response(response)
        except Exception as exc:
            print(f"[TwitterCollector] get_tweets API failed: {exc}")
            return []

    async def _search(self, query: str, max_results: int) -> list[RawPostData]:
        try:
            import tweepy
            response = await self._client.search_recent_tweets(
                query=query,
                max_results=max_results,
                tweet_fields=["created_at", "author_id", "public_metrics"],
                expansions=["author_id"],
                user_fields=["username", "name"],
            )
        except Exception as exc:
            print(f"[TwitterCollector] search failed for query={query!r}: {exc}")
            return []
        return self._parse_response(response)

    async def _user_timeline(self, user_id: str, max_results: int) -> list[RawPostData]:
        try:
            response = await self._client.get_users_tweets(
                id=user_id,
                max_results=max_results,
                tweet_fields=["created_at", "author_id", "public_metrics"],
                exclude=["retweets", "replies"],
            )
        except Exception as exc:
            print(f"[TwitterCollector] timeline failed for user_id={user_id!r}: {exc}")
            return []
        return self._parse_response(response)

    async def _user_timeline_by_username(
        self, username: str, since: datetime | None
    ) -> list[RawPostData]:
        try:
            user_resp = await self._client.get_user(username=username)
            if not user_resp.data:
                return []
            return await self._user_timeline(
                str(user_resp.data.id),
                min(settings.collector_max_results_per_query, 100),
            )
        except Exception as exc:
            print(f"[TwitterCollector] user timeline by username failed for {username!r}: {exc}")
            return []

    async def _fetch_conversation(
        self, tweet_id: str, since: datetime | None
    ) -> list[RawPostData]:
        try:
            response = await self._client.search_recent_tweets(
                query=f"conversation_id:{tweet_id}",
                max_results=10,
                tweet_fields=["created_at", "author_id", "public_metrics"],
                expansions=["author_id"],
                user_fields=["username"],
            )
            return self._parse_response(response)
        except Exception as exc:
            print(f"[TwitterCollector] conversation fetch failed for {tweet_id!r}: {exc}")
            return []

    def _parse_response(self, response) -> list[RawPostData]:
        if not response.data:
            return []

        user_map: dict[str, str] = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                user_map[str(user.id)] = user.username

        posts: list[RawPostData] = []
        for tweet in response.data:
            author_id = str(tweet.author_id) if tweet.author_id else None
            author_name = user_map.get(author_id or "", author_id or "unknown")
            metrics = tweet.public_metrics or {}
            posted_at: datetime = tweet.created_at or datetime.now(timezone.utc)
            if posted_at.tzinfo is not None:
                posted_at = posted_at.replace(tzinfo=None)

            posts.append(
                RawPostData(
                    source=self.source_name,
                    external_id=str(tweet.id),
                    content=tweet.text,
                    author_name=author_name,
                    author_id=author_id,
                    url=f"https://twitter.com/i/web/status/{tweet.id}",
                    posted_at=posted_at,
                    metadata={
                        "likes": metrics.get("like_count", 0),
                        "retweets": metrics.get("retweet_count", 0),
                        "replies": metrics.get("reply_count", 0),
                    },
                )
            )
        return posts

    # ------------------------------------------------------------------
    # Syndication API（无需认证，支持 X Article）
    # ------------------------------------------------------------------

    async def _collect_by_ids_syndication(self, tweet_ids: list[str]) -> list[RawPostData]:
        posts: list[RawPostData] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for tweet_id in tweet_ids:
                post = await _fetch_syndication(client, tweet_id)
                if post:
                    posts.append(post)
        return posts


# ---------------------------------------------------------------------------
# Syndication API 辅助函数
# ---------------------------------------------------------------------------


async def _fetch_article_full_content(
    client: httpx.AsyncClient, article_url: str
) -> str | None:
    """通过 Jina Reader 获取 X 长文全文。

    若配置了 TWITTER_AUTH_TOKEN + TWITTER_CT0，则通过 X-Set-Cookie 传递登录态，
    可突破 X Article 的登录墙，获取完整正文。失败时返回 None。
    """
    jina_url = f"https://r.jina.ai/{article_url}"
    headers: dict[str, str] = {
        "Accept": "text/plain",
        # 不传 User-Agent：让 Jina 用自己默认的 UA 访问 X.com
        # 自定义 UA 会被 Jina 透传给 X.com，导致 X.com 返回 403
        "X-No-Cache": "true",
        # 给 Jina 足够时间等待 X Article JS 渲染完成
        "X-Timeout": "30",
    }
    # 注入 X 登录 Cookie，让 Jina Reader 以登录态访问 Article
    auth_token = settings.twitter_auth_token
    ct0 = settings.twitter_ct0
    if auth_token and ct0:
        headers["X-Set-Cookie"] = f"auth_token={auth_token}; ct0={ct0}"

    try:
        # 必须用独立的新 client，不能复用外层 client（外层已连接 Twitter CDN，
        # 共用会导致 Jina 返回 403）。
        # 短暂延迟以避免 Syndication 请求后立即触发 Jina 速率限制
        await asyncio.sleep(1)
        async with httpx.AsyncClient(timeout=45) as jina_client:
            resp = await jina_client.get(jina_url, headers=headers)
            text = resp.text.strip() if resp.status_code == 200 else ""
        if len(text) > 200 and "Sign in" not in text[:300]:
            return text
    except Exception as exc:
        print(f"[TwitterCollector] Jina reader fetch failed for {article_url}: {exc}")
    return None


async def _fetch_syndication(
    client: httpx.AsyncClient, tweet_id: str
) -> RawPostData | None:
    """通过 Syndication API 抓取单条推文（无需认证，支持 X Article）。

    token 使用 Vercel react-tweet 逆向出的计算公式，直接传 0 在新推文上已不可靠。
    """
    token = _get_syndication_token(tweet_id)
    try:
        resp = await client.get(
            _SYNDICATION_URL,
            params={"id": tweet_id, "lang": "en", "token": token},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                # Syndication API 需要来自 platform.twitter.com 的 Referer
                "Referer": "https://platform.twitter.com/",
                "Origin": "https://platform.twitter.com",
            },
        )
        if resp.status_code == 404:
            print(f"[TwitterCollector] tweet {tweet_id} not found (404)")
            return None
        resp.raise_for_status()
        data = resp.json()

        # 推文已删除或账号被封
        if data.get("tombstone") or data.get("notFound"):
            print(f"[TwitterCollector] tweet {tweet_id} tombstoned or not found")
            return None

        # X Article：尝试通过 Jina Reader 获取全文
        full_article_content: str | None = None
        article = data.get("article")
        if article:
            article_rest_id = article.get("rest_id", "")
            if article_rest_id:
                article_url = f"https://x.com/i/article/{article_rest_id}"
                full_article_content = await _fetch_article_full_content(client, article_url)

        return _parse_syndication_data(data, full_article_content)
    except Exception as exc:
        print(f"[TwitterCollector] syndication fetch failed for {tweet_id}: {exc}")
        return None


def _parse_syndication_data(data: dict, full_article_content: str | None = None) -> RawPostData:
    """将 Syndication API 响应解析为 RawPostData。"""
    tweet_id = data["id_str"]
    user = data.get("user", {})

    # X Article：优先使用 Jina Reader 抓取的全文，否则降级为预览
    article = data.get("article")
    if article:
        if full_article_content:
            content = full_article_content
        else:
            title = article.get("title", "")
            preview = article.get("preview_text", "")
            article_rest_id = article.get("rest_id", "")
            article_url = f"https://x.com/i/article/{article_rest_id}" if article_rest_id else ""
            content = (
                f"[X长文·仅预览] {title}\n\n"
                f"{preview}\n\n"
                f"[注：以上为系统自动截取的预览摘要，X长文全文需登录后查看"
                + (f"，原文链接：{article_url}" if article_url else "")
                + "]"
            )
    else:
        # Note Tweet（长推文）：优先取 note_tweet 字段的完整文本
        note_tweet = data.get("note_tweet", {})
        if note_tweet:
            note_result = (
                note_tweet.get("note_tweet_results", {})
                .get("result", {})
            )
            text = note_result.get("text", "") or data.get("text", "")
            # note_tweet 的实体可能在单独字段中
            entities = note_result.get("entity_set", data.get("entities", {}))
        else:
            text = data.get("text", "")
            entities = data.get("entities", {})
        # 展开 t.co 短链为实际 URL
        for u in entities.get("urls", []):
            text = text.replace(u["url"], u["expanded_url"])
        content = text

    # ── 提取媒体（图片、视频）────────────────────────────────────────────────
    media_items: list[dict] = []
    for m in data.get("mediaDetails", []):
        mtype = m.get("type", "")
        if mtype == "photo":
            url = m.get("media_url_https", "")
            if url:
                # :orig 后缀获取原始尺寸
                media_items.append({"type": "photo", "url": url + ":orig"})
        elif mtype in ("video", "animated_gif"):
            variants = m.get("video_info", {}).get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4_variants:
                best = max(mp4_variants, key=lambda v: v.get("bitrate", 0))
                media_items.append({
                    "type": "video" if mtype == "video" else "gif",
                    "url": best["url"],
                })

    screen_name = user.get("screen_name", "unknown")
    return RawPostData(
        source="twitter",
        external_id=tweet_id,
        content=content,
        author_name=user.get("name", "unknown"),
        author_id=str(user.get("id_str") or user.get("id") or ""),
        url=f"https://twitter.com/{screen_name}/status/{tweet_id}",
        posted_at=_parse_tweet_time(data.get("created_at", "")),
        metadata={
            "likes": data.get("favorite_count", 0),
            "retweets": data.get("retweet_count", 0),
            "replies": data.get("conversation_count", 0),
            "is_article": bool(article),
        },
        media_items=media_items,
    )


def _parse_tweet_time(raw: str) -> datetime:
    """解析推文时间，支持 ISO 8601（Syndication API）和 RFC 2822（官方 API）两种格式。"""
    if not raw:
        return datetime.utcnow()
    # ISO 8601: "2026-02-24T12:12:41.000Z"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except ValueError:
        pass
    # RFC 2822: "Mon, 24 Feb 2026 12:12:41 +0000"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
