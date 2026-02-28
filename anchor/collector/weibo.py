"""微博采集器

访客模式（默认，无需账号）：
  自动通过 Sina Visitor System 获取访客 Cookie（genvisitor + incarnate 流程），
  参考 yt-dlp weibo extractor（2025 年持续维护）。

账号模式（可选，更稳定）：
  在 .env 中配置 WEIBO_COOKIE="SUB=xxx; SUBP=yyy"（从浏览器 DevTools 复制）。

主要端点：
  - 单条微博：https://weibo.com/ajax/statuses/show?id={mid}
  - 用户时间线：https://weibo.com/ajax/profile/getWaterFallContent?uid={uid}
  - 搜索：https://m.weibo.cn/api/container/getIndex（containerid=100103type=1&q=关键词）
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime
from typing import Any

import httpx

from anchor.collector.base import BaseCollector, RawPostData
from anchor.config import settings


_WEIBO_TOPICS = [
    "经济预测",
    "股市分析",
    "A股行情",
    "人民币汇率",
    "降息加息",
    "宏观经济",
    "大宗商品",
    "美股分析",
]

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://weibo.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


async def _generate_visitor_cookies(client: httpx.AsyncClient) -> None:
    """通过 Sina Visitor System 获取访客 Cookie（无需账号）。

    流程：POST genvisitor 获取 tid → GET incarnate 激活 session → Cookie 自动写入 client.cookies。
    参考 yt-dlp weibo extractor（yt_dlp/extractor/weibo.py，2025 年持续维护）。
    """
    try:
        resp = await client.post(
            "https://passport.weibo.com/visitor/genvisitor",
            data={
                "cb": "gen_callback",
                "fp": json.dumps(
                    {
                        "os": "1",
                        "browser": "Chrome120,0,0,0",
                        "fonts": "undefined",
                        "screenInfo": "1920*1080*24",
                        "plugins": "",
                    },
                    separators=(",", ":"),
                ),
            },
        )
        # 响应为 JSONP 格式：gen_callback({...})
        m = re.search(r"gen_callback\((.*)\)", resp.text)
        if not m:
            print("[WeiboCollector] genvisitor: unexpected response format")
            return
        payload = json.loads(m.group(1))
        data_part = payload.get("data", {})
        tid = data_part.get("tid", "")
        confidence = data_part.get("confidence", 100)
        new_tid = data_part.get("new_tid", False)
        w = 3 if new_tid else 2
        if not tid:
            print("[WeiboCollector] genvisitor: no tid in response")
            return

        # incarnate：激活访客 session，服务器 Set-Cookie 写入 client.cookies
        tid_encoded = tid.replace("+", "%2b").replace("=", "%3d")
        await client.get(
            "https://passport.weibo.com/visitor/visitor",
            params={
                "a": "incarnate",
                "t": tid_encoded,
                "w": w,
                "c": f"{confidence:03d}",
                "gc": "",
                "cb": "cross_domain",
                "from": "weibo",
                "_rand": random.random(),
            },
        )
        print("[WeiboCollector] visitor cookies acquired")
    except Exception as exc:
        print(f"[WeiboCollector] visitor cookie generation failed: {exc}")


class WeiboCollector(BaseCollector):
    """微博采集器（访客模式 / 账号模式自动切换）"""

    def __init__(self, topics: list[str] | None = None) -> None:
        self._topics = topics or _WEIBO_TOPICS
        # 优先使用账号 Cookie；为空则走访客模式
        self._weibo_cookie = settings.weibo_cookie

    @property
    def source_name(self) -> str:
        return "weibo"

    def _make_headers(self) -> dict[str, str]:
        h = dict(_BASE_HEADERS)
        if self._weibo_cookie:
            h["Cookie"] = self._weibo_cookie
        return h

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def collect_by_ids(self, weibo_ids: list[str]) -> list[RawPostData]:
        """按微博 ID（mid 或 bid）列表抓取单条微博。"""
        posts: list[RawPostData] = []
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers=self._make_headers()
        ) as client:
            if not self._weibo_cookie:
                await _generate_visitor_cookies(client)
            for wid in weibo_ids:
                post = await self._fetch_post(client, wid)
                if post:
                    posts.append(post)
        return posts

    async def collect(
        self,
        topics: list[str] | None = None,
        uids: list[str] | None = None,
        **_: Any,
    ) -> list[RawPostData]:
        """批量采集：话题搜索 + 指定用户时间线。"""
        posts: list[RawPostData] = []
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers=self._make_headers()
        ) as client:
            if not self._weibo_cookie:
                await _generate_visitor_cookies(client)
            for topic in topics or self._topics:
                posts.extend(await self._search_topic(client, topic))
            for uid in uids or []:
                posts.extend(await self._fetch_user_timeline(client, uid))
        return posts

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _fetch_post(
        self, client: httpx.AsyncClient, weibo_id: str
    ) -> RawPostData | None:
        """通过 ajax API 抓取单条微博完整内容。

        若 isLongText=True（超长微博被截断），额外调用 longtext 端点获取全文。
        text_raw 以零宽空格 ​ 结尾表示截断，全文通过 longTextContent 字段返回。
        """
        try:
            resp = await client.get(
                "https://weibo.com/ajax/statuses/show",
                params={"id": weibo_id},
            )
            resp.raise_for_status()
            status = resp.json()

            # 超长微博：text_raw 仍被截断，需调用 longtext 端点
            # longtext 端点需要 bid（base62），不接受 mid（数字）
            if status.get("isLongText"):
                longtext_id = str(status.get("bid") or status.get("mid") or status.get("id") or weibo_id)
                try:
                    lt_resp = await client.get(
                        "https://weibo.com/ajax/statuses/longtext",
                        params={"id": longtext_id},
                    )
                    if lt_resp.status_code == 200:
                        full_text = (
                            lt_resp.json().get("data", {}).get("longTextContent", "")
                        )
                        if full_text:
                            # longTextContent 含 HTML（<a>、<br/> 等），需过滤
                            status["text_raw"] = _strip_html(full_text)
                except Exception as lt_exc:
                    print(f"[WeiboCollector] longtext fetch failed for {longtext_id!r}: {lt_exc}")

            return self._parse_status(status)
        except Exception as exc:
            print(f"[WeiboCollector] fetch post failed for {weibo_id!r}: {exc}")
            return None

    async def _search_topic(
        self, client: httpx.AsyncClient, keyword: str
    ) -> list[RawPostData]:
        """通过 m.weibo.cn 搜索话题关键词。

        长文（isLongText=True）会额外调用 ajax/statuses/show 获取完整文本。
        """
        try:
            resp = await client.get(
                "https://m.weibo.cn/api/container/getIndex",
                params={
                    "containerid": f"100103type=1&q={keyword}",
                    "page_type": "searchall",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[WeiboCollector] search failed for {keyword!r}: {exc}")
            return []

        posts: list[RawPostData] = []
        for card in data.get("data", {}).get("cards", []):
            for mblog in _iter_mblogs(card):
                mid = str(mblog.get("mid", mblog.get("id", "")))
                # 长文被截断：通过 ajax API 获取 text_raw 完整内容
                if mblog.get("isLongText") or mblog.get("longText"):
                    full = await self._fetch_post(client, mid)
                    if full:
                        posts.append(full)
                        continue
                posts.append(self._parse_mblog(mblog))
        return posts

    async def _fetch_user_timeline(
        self, client: httpx.AsyncClient, uid: str
    ) -> list[RawPostData]:
        """抓取用户时间线（ajax 接口，比 m.weibo.cn 更稳定）。"""
        try:
            resp = await client.get(
                "https://weibo.com/ajax/profile/getWaterFallContent",
                params={"uid": uid},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[WeiboCollector] user timeline failed for uid={uid!r}: {exc}")
            return []

        return [self._parse_status(s) for s in data.get("statuses", []) if s]

    def _parse_status(self, status: dict) -> RawPostData:
        """解析 weibo.com/ajax/statuses/show 响应。

        text_raw 是纯文本完整内容，优先使用；降级为 text（含 HTML）时去标签。
        """
        user = status.get("user") or {}
        text = status.get("text_raw") or _strip_html(status.get("text", ""))
        uid = str(user.get("id", ""))
        mid = str(status.get("mid", status.get("id", "")))
        bid = status.get("bid", mid)  # bid 是 base62 短 ID，构成微博链接
        return RawPostData(
            source=self.source_name,
            external_id=mid,
            content=text,
            author_name=user.get("screen_name", "unknown"),
            author_id=uid,
            url=f"https://weibo.com/{uid}/{bid}",
            posted_at=_parse_weibo_time(status.get("created_at", "")),
            metadata={
                "likes": status.get("attitudes_count", 0),
                "reposts": status.get("reposts_count", 0),
                "comments": status.get("comments_count", 0),
                "followers": user.get("followers_count", 0),
            },
            media_items=_extract_weibo_media(status),
        )

    def _parse_mblog(self, mblog: dict) -> RawPostData:
        """解析搜索结果中的 mblog 对象（text 字段，含 HTML，短文 fallback）。"""
        user = mblog.get("user") or {}
        text = _strip_html(mblog.get("text", ""))
        uid = str(user.get("id", ""))
        mid = str(mblog.get("mid", mblog.get("id", "")))
        bid = mblog.get("bid", mid)
        return RawPostData(
            source=self.source_name,
            external_id=mid,
            content=text,
            author_name=user.get("screen_name", "unknown"),
            author_id=uid,
            url=f"https://weibo.com/{uid}/{bid}",
            posted_at=_parse_weibo_time(mblog.get("created_at", "")),
            metadata={
                "likes": mblog.get("attitudes_count", 0),
                "reposts": mblog.get("reposts_count", 0),
                "comments": mblog.get("comments_count", 0),
                "followers": user.get("followers_count", 0),
            },
            media_items=_extract_weibo_media(mblog),
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _extract_weibo_media(status: dict) -> list[dict]:
    """从微博 status/mblog 对象中提取图片和视频 URL 列表。"""
    items: list[dict] = []

    # 图片：pic_ids 决定顺序，pic_infos 提供 URL
    pic_ids = status.get("pic_ids", [])
    pic_infos = status.get("pic_infos", {})
    for pid in pic_ids:
        info = pic_infos.get(pid, {})
        # 按画质从高到低依次尝试
        source = info.get("original") or info.get("large") or info.get("bmiddle") or {}
        url = source.get("url", "")
        if url:
            items.append({"type": "photo", "url": url})

    # 视频：page_info.type == "video"
    page_info = status.get("page_info") or {}
    if page_info.get("type") == "video":
        media_info = page_info.get("media_info") or {}
        video_url = (
            media_info.get("stream_url_hd")
            or media_info.get("stream_url")
            or ""
        )
        if video_url:
            items.append({"type": "video", "url": video_url})

    return items


def _iter_mblogs(card: dict):
    """从 card 对象中递归提取 mblog（支持嵌套 card_group）。"""
    mblog = card.get("mblog")
    if mblog:
        yield mblog
    for sub in card.get("card_group", []):
        sub_mblog = sub.get("mblog")
        if sub_mblog:
            yield sub_mblog


def _parse_weibo_time(raw: str) -> datetime:
    """解析微博时间格式，如 'Wed Feb 26 10:00:00 +0800 2026'"""
    if not raw:
        return datetime.utcnow()
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _strip_html(text: str) -> str:
    """去除 HTML 标签（微博搜索结果的 text 字段含 <a> 等标签）"""
    return re.sub(r"<[^>]+>", "", text).strip()
