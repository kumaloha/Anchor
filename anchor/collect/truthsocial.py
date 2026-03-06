"""Truth Social 采集器

无需 token，通过 Jina Reader 渲染页面获取内容。
Jina Reader: https://r.jina.ai/{url}

支持：
  - 单帖抓取（按帖子 URL / ID + username）
  - 用户主页抓取（解析主页 HTML 列表，Jina 渲染后提取）

URL 格式：https://truthsocial.com/@username/posts/{id}
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from loguru import logger

from anchor.collect.base import BaseCollector, RawPostData

_JINA_BASE = "https://r.jina.ai/"


class TruthSocialCollector(BaseCollector):
    """Truth Social 采集器，通过 Jina Reader 渲染页面（无需 token）。"""

    @property
    def source_name(self) -> str:
        return "truthsocial"

    async def collect_by_ids(
        self, post_ids: list[str], usernames: list[str] | None = None
    ) -> list[RawPostData]:
        """按帖子 ID 抓取。需要同时传入 usernames（与 post_ids 一一对应），
        或在 post_ids 中直接传入完整 URL。"""
        results: list[RawPostData] = []
        for i, post_id in enumerate(post_ids):
            if post_id.startswith("http"):
                url = post_id
            elif usernames and i < len(usernames):
                url = f"https://truthsocial.com/@{usernames[i]}/posts/{post_id}"
            else:
                logger.warning(f"[TruthSocial] no username for post_id={post_id}, skip")
                continue
            post = await _fetch_via_jina(url)
            if post:
                results.append(post)
        return results

    async def collect_by_url(self, url: str) -> RawPostData | None:
        """直接按完整 URL 抓取单帖。"""
        return await _fetch_via_jina(url)

    async def collect(self, **kwargs) -> list[RawPostData]:
        if urls := kwargs.get("urls"):
            results = []
            for url in urls:
                post = await _fetch_via_jina(url)
                if post:
                    results.append(post)
            return results
        return []


# ---------------------------------------------------------------------------
# Jina Reader 抓取 + 解析
# ---------------------------------------------------------------------------


async def _fetch_via_jina(post_url: str) -> RawPostData | None:
    """通过 Jina Reader + curl 获取 Truth Social 帖子内容。

    使用系统 curl 而非 httpx，以避免 Cloudflare TLS 指纹检测。
    """
    jina_url = _JINA_BASE + post_url
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s",
            "-H", "Accept: text/plain",
            "--max-time", "35",
            jina_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=40)
        text = stdout.decode("utf-8", errors="replace")
        # 检测 Jina 无法穿透 Cloudflare 的几种失败响应
        if not text or "403: Forbidden" in text or "CAPTCHA" in text or "Just a moment" in text:
            logger.warning(f"[TruthSocial] Jina blocked (403/CAPTCHA) for {post_url}")
            return None
        return _parse_jina_response(text, post_url)
    except Exception as exc:
        logger.error(f"[TruthSocial] Jina/curl failed for {post_url}: {exc}")
        return None


def _parse_jina_response(text: str, post_url: str) -> RawPostData | None:
    """解析 Jina Reader 返回的 markdown 文本。

    格式（示例）：
        Title: Truth Details | Truth Social
        URL Source: https://truthsocial.com/@realDonaldTrump/posts/...
        Published Time: Tue, 03 Mar 2026 15:45:25 GMT
        Markdown Content:
        [帖子正文]
    """
    # ── 提取正文 ──────────────────────────────────────────────────────────
    content_match = re.search(r"Markdown Content:\s*\n(.*)", text, re.DOTALL)
    if not content_match:
        logger.warning(f"[TruthSocial] no Markdown Content section in Jina response")
        return None
    content = content_match.group(1).strip()

    if not content or len(content) < 10:
        logger.warning(f"[TruthSocial] empty content from Jina for {post_url}")
        return None

    # ── 提取发帖时间 ──────────────────────────────────────────────────────
    time_match = re.search(r"Published Time:\s*(.+)", text)
    posted_at = _parse_time(time_match.group(1).strip() if time_match else "")

    # ── 从 URL 提取作者和帖子 ID ─────────────────────────────────────────
    url_match = re.search(r"truthsocial\.com/@([\w.]+)/posts/(\d+)", post_url)
    if url_match:
        username = url_match.group(1)
        post_id = url_match.group(2)
    else:
        username = "unknown"
        post_id = re.sub(r"[^0-9]", "", post_url)[-18:] or "0"

    # ── 作者显示名：尝试从 URL source 行再次确认 ─────────────────────────
    # Jina 不提供显示名，用 @username 代替
    author_name = username

    return RawPostData(
        source="truthsocial",
        external_id=post_id,
        content=content,
        author_name=author_name,
        author_id=username,
        url=f"https://truthsocial.com/@{username}/posts/{post_id}",
        posted_at=posted_at,
        metadata={"likes": 0, "retweets": 0, "replies": 0},
        media_items=[],
    )


def _parse_time(raw: str) -> datetime:
    if not raw:
        return datetime.utcnow()
    try:
        return parsedate_to_datetime(raw).replace(tzinfo=None)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()
