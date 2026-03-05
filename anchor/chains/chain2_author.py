"""
Chain 2 — 作者分析链路
======================
输入：author_id
输出：写入 DB 的 Author 档案 + AuthorStanceProfile

流程：
  author_id
      → AuthorProfiler().profile()（写 role/expertise/credibility_tier）
      → 从 DB 读取作者近期 RawPost 内容（最多 10 条）
      → LLM 分析立场/受众/核心信息
      → 写入 AuthorStanceProfile

LLM 输出格式：
  {
    "stance_label": "看涨/多头|看跌/空头|中立/客观|警告/防御|批判/质疑|政策倡导|教育/分析",
    "audience": "目标受众（≤40字）",
    "core_message": "核心信息（≤80字）",
    "author_summary": "以...身份，持...立场，向...传达...（≤100字）"
  }

用法：
  async with AsyncSessionLocal() as session:
      result = await run_chain2(author_id=1, session=session)
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Author, AuthorStanceProfile, RawPost, _utcnow
from anchor.verify.author_profiler import AuthorProfiler

_MAX_TOKENS = 1200

# ---------------------------------------------------------------------------
# 立场分析提示词
# ---------------------------------------------------------------------------

_STANCE_SYSTEM = """\
你是一名专业媒体分析师，擅长识别意见领袖的立场倾向和传播目的。
给定一位作者的近期帖子集合，分析其整体立场和受众定位。

立场标签分类：
  看涨/多头   — 对某类资产/经济前景持乐观态度
  看跌/空头   — 对某类资产/经济前景持悲观态度
  中立/客观   — 平衡呈现多方观点，不明确表态
  警告/防御   — 强调风险和防御性策略
  批判/质疑   — 对现有政策/机构/观点提出质疑
  政策倡导    — 推动特定政策或政治立场
  教育/分析   — 主要以教育或学术分析为目的

输出必须是合法 JSON，不加任何其他文字。\
"""

_STANCE_USER_TEMPLATE = """\
## 作者信息
姓名：{author_name}
平台：{platform}
职业角色：{role}
专业领域：{expertise}

## 近期帖子（共 {post_count} 条）
{posts_text}

## 任务
基于以上帖子内容，分析该作者的整体立场倾向和传播目的。

严格输出 JSON：

```json
{{
  "stance_label": "看涨/多头|看跌/空头|中立/客观|警告/防御|批判/质疑|政策倡导|教育/分析",
  "audience": "目标受众（≤40字）",
  "core_message": "核心信息（≤80字）",
  "author_summary": "以...身份，持...立场，向...传达...（≤100字）"
}}
```\
"""

_VALID_STANCE_LABELS = {
    "看涨/多头", "看跌/空头", "中立/客观", "警告/防御",
    "批判/质疑", "政策倡导", "教育/分析",
}

_MAX_POSTS = 10       # 用于立场分析的最近帖子数量
_MAX_POST_CHARS = 400  # 每条帖子的最大字符数（截断）


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def run_chain2(author_id: int, session: AsyncSession) -> dict:
    """执行链路2：作者档案分析 + 立场分析

    Args:
        author_id: authors 表主键
        session:   异步数据库 Session

    Returns:
        dict with keys:
          author_id, author_name, role, credibility_tier,
          stance_label, audience, core_message, author_summary
    """
    # ── Step 1：加载作者 ─────────────────────────────────────────────────
    author = await session.get(Author, author_id)
    if not author:
        raise ValueError(f"Author id={author_id} not found")

    logger.info(f"[Chain2] Analyzing author: {author.name} (id={author_id})")

    # ── Step 2：AuthorProfiler 档案分析 ──────────────────────────────────
    await AuthorProfiler().profile(author, session)
    await session.flush()
    # 重新加载（profile 可能修改了字段）
    await session.refresh(author)

    # ── Step 3：读取近期帖子 ─────────────────────────────────────────────
    posts_result = await session.exec(
        select(RawPost)
        .where(RawPost.author_platform_id == author.platform_id)
        .where(RawPost.source == author.platform)
        .order_by(RawPost.posted_at.desc())
        .limit(_MAX_POSTS)
    )
    recent_posts = list(posts_result.all())

    if not recent_posts:
        logger.info(f"[Chain2] No recent posts for author id={author_id}, skipping stance analysis")
        return _build_result(author, None)

    # ── Step 4：LLM 立场分析 ─────────────────────────────────────────────
    posts_text = _format_posts(recent_posts)
    prompt = _STANCE_USER_TEMPLATE.format(
        author_name=author.name,
        platform=author.platform,
        role=author.role or "未知",
        expertise=author.expertise_areas or "未知",
        post_count=len(recent_posts),
        posts_text=posts_text,
    )

    resp = await chat_completion(
        system=_STANCE_SYSTEM,
        user=prompt,
        max_tokens=_MAX_TOKENS,
    )

    parsed = _parse_json(resp.content) if resp else None

    # ── Step 5：写入 AuthorStanceProfile ─────────────────────────────────
    await _upsert_stance_profile(author_id, parsed, session)
    await session.flush()

    logger.info(
        f"[Chain2] Done: author={author.name} "
        f"tier={author.credibility_tier} "
        f"stance={parsed.get('stance_label') if parsed else 'N/A'}"
    )

    return _build_result(author, parsed)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _format_posts(posts: list[RawPost]) -> str:
    """格式化帖子列表为分析文本。"""
    lines: list[str] = []
    for i, post in enumerate(posts, 1):
        content = (post.enriched_content or post.content or "").strip()
        if len(content) > _MAX_POST_CHARS:
            content = content[:_MAX_POST_CHARS] + "…"
        lines.append(f"[{i}] ({post.posted_at.strftime('%Y-%m-%d') if post.posted_at else '?'})\n{content}")
    return "\n\n".join(lines)


async def _upsert_stance_profile(
    author_id: int, parsed: dict | None, session: AsyncSession
) -> None:
    """更新或创建 AuthorStanceProfile。"""
    result = await session.exec(
        select(AuthorStanceProfile).where(AuthorStanceProfile.author_id == author_id)
    )
    profile = result.first()

    if profile is None:
        profile = AuthorStanceProfile(author_id=author_id)

    if parsed:
        stance = parsed.get("stance_label")
        if stance and stance not in _VALID_STANCE_LABELS:
            logger.warning(f"[Chain2] Invalid stance_label={stance!r}, ignoring")
            stance = None

        if stance:
            # 更新分布计数
            dist: dict[str, int] = {}
            if profile.stance_distribution:
                try:
                    dist = json.loads(profile.stance_distribution)
                except Exception:
                    pass
            dist[stance] = dist.get(stance, 0) + 1
            profile.stance_distribution = json.dumps(dist, ensure_ascii=False)

            # 更新主导立场
            total = sum(dist.values())
            profile.total_analyzed = total
            dominant = max(dist, key=dist.get)
            profile.dominant_stance = dominant
            profile.dominant_stance_ratio = dist[dominant] / total if total else 0.0

        profile.audience = _safe_str(parsed.get("audience"))
        profile.core_message = _safe_str(parsed.get("core_message"))
        profile.author_summary = _safe_str(parsed.get("author_summary"))

    profile.last_updated = _utcnow()
    session.add(profile)


def _parse_json(raw: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[Chain2] JSON parse error: {exc}")
        return None


def _safe_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _build_result(author: Author, parsed: dict | None) -> dict:
    return {
        "author_id": author.id,
        "author_name": author.name,
        "role": author.role,
        "credibility_tier": author.credibility_tier,
        "expertise_areas": author.expertise_areas,
        "stance_label": parsed.get("stance_label") if parsed else None,
        "audience": parsed.get("audience") if parsed else None,
        "core_message": parsed.get("core_message") if parsed else None,
        "author_summary": parsed.get("author_summary") if parsed else None,
    }
