"""
评论采集器
==========
从微博/Twitter 抓取帖子的评论，供舆情分析器（OpinionAnalyzer）使用。

返回标准化的 CommentItem 列表（不直接入库，由 OpinionAnalyzer 处理后写入）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from loguru import logger


@dataclass
class RawComment:
    external_id: str
    content: str
    author_name: str
    author_id: str
    likes: int
    posted_at: datetime
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 微博评论
# ---------------------------------------------------------------------------


async def fetch_weibo_comments(
    mid: str,
    max_count: int = 50,
) -> list[RawComment]:
    """抓取微博帖子的热门评论。

    使用移动端 hotflow 接口（无需登录），按热度排序。
    Args:
        mid: 微博帖子的数字 ID（如 "5269197421548712"）
        max_count: 最多抓取条数（每页约20条，自动翻页）
    """
    comments: list[RawComment] = []
    max_id = 0
    max_id_type = 0
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
        "Referer": f"https://m.weibo.cn/detail/{mid}",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        while len(comments) < max_count:
            params: dict = {
                "id": mid,
                "mid": mid,
                "max_id_type": max_id_type,
            }
            if max_id:
                params["max_id"] = max_id

            try:
                resp = await client.get(
                    "https://m.weibo.cn/comments/hotflow",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(f"[CommentCollector] Weibo comment fetch failed for mid={mid}: {exc}")
                break

            if data.get("ok") != 1:
                break

            page_data = data.get("data", {})
            page_comments = page_data.get("data", [])
            if not page_comments:
                break

            for c in page_comments:
                parsed = _parse_weibo_comment(c)
                if parsed:
                    comments.append(parsed)

            # 翻页
            next_max_id = page_data.get("max_id", 0)
            if not next_max_id or next_max_id == max_id:
                break
            max_id = next_max_id
            max_id_type = page_data.get("max_id_type", 0)

    logger.info(f"[CommentCollector] Weibo mid={mid}: fetched {len(comments)} comments")
    return comments[:max_count]


def _parse_weibo_comment(raw: dict) -> RawComment | None:
    user = raw.get("user") or {}
    text = _strip_html(raw.get("text", ""))
    if not text:
        return None
    posted_at = _parse_weibo_time(raw.get("created_at", ""))
    return RawComment(
        external_id=str(raw.get("id", "")),
        content=text,
        author_name=user.get("screen_name", "unknown"),
        author_id=str(user.get("id", "")),
        likes=raw.get("like_counts", 0),
        posted_at=posted_at,
        metadata={"floor_number": raw.get("floor_number", 0)},
    )


# ---------------------------------------------------------------------------
# Twitter 回复
# ---------------------------------------------------------------------------


async def fetch_twitter_replies(
    tweet_id: str,
    max_count: int = 50,
) -> list[RawComment]:
    """抓取 Twitter 推文的回复（使用 conversation_id 搜索）。

    需要在 .env 中配置 TWITTER_BEARER_TOKEN。
    """
    try:
        import tweepy
        from anchor.config import settings

        if not settings.twitter_bearer_token:
            logger.warning("[CommentCollector] TWITTER_BEARER_TOKEN not set, skipping replies")
            return []

        client = tweepy.AsyncClient(
            bearer_token=settings.twitter_bearer_token,
            wait_on_rate_limit=True,
        )

        # 先获取原推以得到 conversation_id
        orig = await client.get_tweet(
            tweet_id, tweet_fields=["conversation_id", "author_id"]
        )
        if not orig.data:
            return []
        conv_id = str(orig.data.conversation_id or tweet_id)

        # 搜索同 conversation 下的回复
        resp = await client.search_recent_tweets(
            query=f"conversation_id:{conv_id} -from:{orig.data.author_id}",
            max_results=min(max_count, 100),
            tweet_fields=["author_id", "created_at", "public_metrics"],
            expansions=["author_id"],
            user_fields=["username"],
        )
        if not resp.data:
            return []

        user_map = {
            str(u.id): u.username
            for u in (resp.includes or {}).get("users", [])
        }

        comments: list[RawComment] = []
        for t in resp.data:
            author_id = str(t.author_id) if t.author_id else ""
            metrics = t.public_metrics or {}
            posted_at = t.created_at.replace(tzinfo=None) if t.created_at else datetime.utcnow()
            comments.append(RawComment(
                external_id=str(t.id),
                content=t.text,
                author_name=user_map.get(author_id, author_id),
                author_id=author_id,
                likes=metrics.get("like_count", 0),
                posted_at=posted_at,
            ))

        logger.info(f"[CommentCollector] Twitter tweet_id={tweet_id}: fetched {len(comments)} replies")
        return comments

    except Exception as exc:
        logger.warning(f"[CommentCollector] Twitter reply fetch failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


async def fetch_comments(
    platform: str,
    external_id: str,
    max_count: int = 50,
) -> list[RawComment]:
    """统一入口，根据平台路由到对应实现。"""
    if platform == "weibo":
        return await fetch_weibo_comments(external_id, max_count)
    if platform == "twitter":
        return await fetch_twitter_replies(external_id, max_count)
    logger.warning(f"[CommentCollector] Unsupported platform: {platform}")
    return []


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_weibo_time(raw: str) -> datetime:
    if not raw:
        return datetime.utcnow()
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        return dt.replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
