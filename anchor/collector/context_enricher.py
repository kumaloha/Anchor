"""
Layer 1 — Step 2: 上下文补全器
================================
社交媒体帖子经常缺乏完整上下文：
  - 引用转发（quote tweet / 微博引用）：包含对他人内容的回应
  - 回复型帖子（reply）：属于某个更长对话的中间部分
  - 线程（thread）：作者分多条帖子连续表达同一观点
  - 续写（微博"长文"折叠 / Twitter 帖子截断）

本模块负责识别以上情况，抓取缺失的上下文，拼接成完整的
enriched_content 字符串，供 Step 3 的 Claude 提取器使用。

主入口：enrich(raw_post, session) -> str
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import RawPost, _utcnow


@dataclass
class ContextPiece:
    """单段上下文片段"""

    role: str           # "quoted" | "parent_reply" | "thread_prev" | "thread_next"
    author: str
    content: str
    url: str


async def enrich(raw_post: RawPost, session: AsyncSession) -> str:
    """为 raw_post 补全上下文，返回拼接后的完整文本。

    同时更新 raw_post.enriched_content / context_fetched / has_context。
    调用方负责 commit。
    """
    if raw_post.context_fetched:
        return raw_post.enriched_content or raw_post.content

    pieces: list[ContextPiece] = []

    if raw_post.source == "twitter":
        pieces = await _enrich_twitter(raw_post)
    elif raw_post.source == "weibo":
        pieces = await _enrich_weibo(raw_post)

    raw_post.context_fetched = True
    raw_post.has_context = bool(pieces)

    if pieces:
        raw_post.enriched_content = _assemble(raw_post.content, pieces)
    else:
        raw_post.enriched_content = raw_post.content

    raw_post.processed_at = _utcnow()
    session.add(raw_post)
    return raw_post.enriched_content


# ---------------------------------------------------------------------------
# 拼接格式
# ---------------------------------------------------------------------------

_ROLE_LABEL = {
    "quoted": "【被引用内容】",
    "parent_reply": "【回复对象】",
    "thread_prev": "【上一条】",
    "thread_next": "【下一条】",
}


def _assemble(main_content: str, pieces: list[ContextPiece]) -> str:
    """将上下文片段与主文本拼接为 Claude 易于理解的结构化文本。"""
    parts: list[str] = []

    # 先放上下文（引用源、线程前文）
    for p in pieces:
        if p.role in ("quoted", "parent_reply", "thread_prev"):
            label = _ROLE_LABEL.get(p.role, f"[{p.role}]")
            parts.append(f"{label}\n作者：{p.author}\n内容：{p.content}")

    # 主文本
    parts.append(f"【主要内容】\n{main_content}")

    # 线程续文
    for p in pieces:
        if p.role == "thread_next":
            parts.append(f"{_ROLE_LABEL['thread_next']}\n{p.content}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Twitter 上下文抓取
# ---------------------------------------------------------------------------

async def _enrich_twitter(raw_post: RawPost) -> list[ContextPiece]:
    """
    Twitter 上下文来源：
      1. referenced_tweets[type=quoted]   → 引用的原推
      2. referenced_tweets[type=replied_to] → 回复的父推
      3. conversation_id 相同的前序推文     → 线程
    """
    try:
        from anchor.config import settings
        if not settings.twitter_bearer_token:
            return []

        import tweepy
        client = tweepy.AsyncClient(
            bearer_token=settings.twitter_bearer_token,
            wait_on_rate_limit=True,
        )

        resp = await client.get_tweet(
            raw_post.external_id,
            tweet_fields=["referenced_tweets", "conversation_id", "author_id", "text"],
            expansions=["referenced_tweets.id", "referenced_tweets.id.author_id"],
            user_fields=["username"],
        )
        if not resp.data:
            return []

        tweet = resp.data
        pieces: list[ContextPiece] = []

        # 建立 tweet_id -> (text, username) 映射
        ref_map: dict[str, tuple[str, str]] = {}
        user_map: dict[str, str] = {}
        if resp.includes:
            for u in resp.includes.get("users", []):
                user_map[str(u.id)] = u.username
            for t in resp.includes.get("tweets", []):
                username = user_map.get(str(t.author_id), "unknown")
                ref_map[str(t.id)] = (t.text, username)

        for ref in (tweet.referenced_tweets or []):
            ref_id = str(ref.id)
            text, author = ref_map.get(ref_id, ("（无法获取内容）", "unknown"))
            role = "quoted" if ref.type == "quoted" else "parent_reply"
            pieces.append(ContextPiece(
                role=role,
                author=author,
                content=text,
                url=f"https://twitter.com/i/web/status/{ref_id}",
            ))

        # 若是线程中的帖子，尝试获取同一 conversation 的前序帖子
        conv_id = str(tweet.conversation_id) if tweet.conversation_id else None
        if conv_id and conv_id != raw_post.external_id:
            thread_pieces = await _fetch_thread_context(client, conv_id, raw_post.external_id)
            pieces.extend(thread_pieces)

        return pieces

    except Exception as exc:
        logger.warning(f"[ContextEnricher] Twitter enrichment failed for {raw_post.external_id}: {exc}")
        return []


async def _fetch_thread_context(client, conversation_id: str, current_id: str) -> list[ContextPiece]:
    """获取同一线程中当前帖子之前的内容（按时间排序取最近 3 条）"""
    try:
        resp = await client.search_recent_tweets(
            query=f"conversation_id:{conversation_id}",
            max_results=10,
            tweet_fields=["author_id", "created_at"],
            expansions=["author_id"],
            user_fields=["username"],
        )
        if not resp.data:
            return []

        user_map = {str(u.id): u.username for u in (resp.includes or {}).get("users", [])}

        pieces: list[ContextPiece] = []
        for t in sorted(resp.data, key=lambda x: x.created_at or ""):
            if str(t.id) == current_id:
                continue
            pieces.append(ContextPiece(
                role="thread_prev",
                author=user_map.get(str(t.author_id), "unknown"),
                content=t.text,
                url=f"https://twitter.com/i/web/status/{t.id}",
            ))
        # 只保留最近 3 条前序帖
        return pieces[-3:]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 微博上下文抓取
# ---------------------------------------------------------------------------

async def _enrich_weibo(raw_post: RawPost) -> list[ContextPiece]:
    """
    微博上下文来源：
      1. retweeted_status（转发的原微博）
      2. 长文折叠内容（longText API）
    """
    try:
        import httpx

        pieces: list[ContextPiece] = []

        # 尝试通过移动端接口获取帖子详情
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://m.weibo.cn/statuses/show",
                params={"id": raw_post.external_id},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.weibo.cn/"},
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})

        # 转发的原微博
        retweeted = data.get("retweeted_status")
        if retweeted:
            user = retweeted.get("user") or {}
            text = _strip_html(retweeted.get("text", ""))
            pieces.append(ContextPiece(
                role="quoted",
                author=user.get("screen_name", "unknown"),
                content=text,
                url="",
            ))

        # 长文折叠（longText）
        long_text = data.get("longText") or {}
        full_text = long_text.get("longTextContent", "")
        if full_text and len(full_text) > len(raw_post.content):
            # 主内容直接用长文替换，无需作为 piece
            raw_post.content = _strip_html(full_text)

        return pieces

    except Exception as exc:
        logger.warning(f"[ContextEnricher] Weibo enrichment failed for {raw_post.external_id}: {exc}")
        return []


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text).strip()
