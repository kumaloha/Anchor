"""
Chain 2 — 作者分析链路（v2）
==============================
输入：raw_post_id（主）→ 自动关联 author
输出：写入 DB 的 Author 档案 + RawPost 内容分类与意图 + AuthorStanceProfile

流程：
  Step 1  作者档案分析（AuthorProfiler，已分析过则跳过）
  Step 2  内容分类 + 作者意图（per-post LLM 分析，写入 RawPost）
  Step 3  作者立场更新（基于近期帖子聚合，写入 AuthorStanceProfile）

内容类型枚举（content_type）：
  市场动向 | 市场分析 | 产业调研 | 公司调研 | 技术论文 | 教育科普 | 政策宣布 | 政策解读

作者意图枚举（author_intent）：
  传递信息 | 影响观点 | 警示风险 | 推荐行动 | 教育科普 | 引发讨论 | 推广宣传

用法：
  async with AsyncSessionLocal() as session:
      result = await run_chain2(post_id=1, session=session)
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

_STANCE_MAX_TOKENS = 1200
_POST_ANALYSIS_MAX_TOKENS = 800
_MAX_POSTS_FOR_STANCE = 10
_MAX_POST_CHARS = 400

_VALID_CONTENT_TYPES = {
    "市场动向", "市场分析", "产业调研", "公司调研",
    "技术论文", "教育科普", "政策宣布", "政策解读",
}

_VALID_INTENTS = {
    "传递信息", "影响观点", "警示风险", "推荐行动",
    "教育科普", "引发讨论", "推广宣传", "政治动员",
}

_VALID_STANCE_LABELS = {
    "看涨/多头", "看跌/空头", "中立/客观", "警告/防御",
    "批判/质疑", "政策倡导", "教育/分析",
}

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


# ---------------------------------------------------------------------------
# 公开函数：前置分类（供 Chain 1 调用）
# ---------------------------------------------------------------------------


async def classify_post(post: RawPost, session: AsyncSession) -> dict:
    """Chain 2 前两步：作者档案 + 内容分类，供 Chain 1 前置调用。

    不含立场分析（Step 3），不依赖 content_summary。
    若 post.chain2_analyzed 已为 True，直接返回 DB 字段，不重复 LLM 调用。
    """
    author = await _get_or_create_author(post, session)
    await AuthorProfiler().profile(author, session)
    await session.flush()
    await session.refresh(author)

    if not post.chain2_analyzed:
        post_analysis = await _analyze_post(post, author)
        if post_analysis:
            ct = post_analysis.get("content_type")
            ct2 = post_analysis.get("content_type_secondary")
            if ct not in _VALID_CONTENT_TYPES:
                logger.warning(f"[Chain2] classify_post: invalid content_type={ct!r}, ignoring")
                ct = None
            if ct2 and ct2 not in _VALID_CONTENT_TYPES:
                ct2 = None
            intent = post_analysis.get("author_intent")
            if intent not in _VALID_INTENTS:
                logger.warning(f"[Chain2] classify_post: invalid author_intent={intent!r}, ignoring")
                intent = None
            post.content_type = ct
            post.content_type_secondary = ct2 if ct2 != ct else None
            post.content_topic = _safe_str(post_analysis.get("content_topic"))
            post.author_intent = intent
            post.intent_note = _safe_str(post_analysis.get("intent_note"))
            # 发文机关（仅政策类内容）
            if ct in {"政策宣布", "政策解读"}:
                ia = _safe_str(post_analysis.get("issuing_authority"))
                al = _safe_str(post_analysis.get("authority_level"))
                if ia:
                    post.issuing_authority = ia
                if al:
                    post.authority_level = al
        post.chain2_analyzed = True
        post.chain2_analyzed_at = _utcnow()
        session.add(post)
        await session.flush()
        logger.info(
            f"[Chain2] classify_post {post.id}: type={post.content_type!r} "
            f"intent={post.author_intent!r} authority={post.issuing_authority!r}"
        )
    else:
        logger.debug(f"[Chain2] classify_post: post {post.id} already analyzed, returning cached fields")

    return {
        "content_type": post.content_type,
        "content_type_secondary": post.content_type_secondary,
        "content_topic": post.content_topic,
        "author_intent": post.author_intent,
        "intent_note": post.intent_note,
        "issuing_authority": post.issuing_authority,
        "authority_level": post.authority_level,
    }


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def run_chain2(post_id: int, session: AsyncSession) -> dict:
    """执行链路2：内容分类 + 作者档案 + 立场分析

    Args:
        post_id:  raw_posts 表主键
        session:  异步数据库 Session

    Returns:
        dict with keys:
          post_id, content_type, content_type_secondary, content_topic,
          author_intent, intent_note,
          author_id, author_name, role, credibility_tier,
          stance_label, audience, core_message, author_summary
    """
    # ── 加载帖子 ──────────────────────────────────────────────────────────
    post = await session.get(RawPost, post_id)
    if not post:
        raise ValueError(f"RawPost id={post_id} not found")

    logger.info(f"[Chain2] Analyzing post id={post_id} (author={post.author_name})")

    # ── 加载/创建作者 ─────────────────────────────────────────────────────
    author = await _get_or_create_author(post, session)

    # ── Step 1：作者档案分析 ──────────────────────────────────────────────
    await AuthorProfiler().profile(author, session)
    await session.flush()
    await session.refresh(author)

    # ── Step 2：内容分类 + 作者意图（per-post）────────────────────────────
    post_analysis: dict | None = None
    if not post.chain2_analyzed:
        post_analysis = await _analyze_post(post, author)
        if post_analysis:
            ct = post_analysis.get("content_type")
            ct2 = post_analysis.get("content_type_secondary")
            if ct not in _VALID_CONTENT_TYPES:
                logger.warning(f"[Chain2] Invalid content_type={ct!r}, ignoring")
                ct = None
            if ct2 and ct2 not in _VALID_CONTENT_TYPES:
                ct2 = None

            intent = post_analysis.get("author_intent")
            if intent not in _VALID_INTENTS:
                logger.warning(f"[Chain2] Invalid author_intent={intent!r}, ignoring")
                intent = None

            post.content_type = ct
            post.content_type_secondary = ct2 if ct2 != ct else None
            post.content_topic = _safe_str(post_analysis.get("content_topic"))
            post.author_intent = intent
            post.intent_note = _safe_str(post_analysis.get("intent_note"))
            # 发文机关（仅政策类内容）
            if ct in {"政策宣布", "政策解读"}:
                ia = _safe_str(post_analysis.get("issuing_authority"))
                al = _safe_str(post_analysis.get("authority_level"))
                if ia:
                    post.issuing_authority = ia
                if al:
                    post.authority_level = al

        post.chain2_analyzed = True
        post.chain2_analyzed_at = _utcnow()
        session.add(post)
        await session.flush()
        logger.info(
            f"[Chain2] Post {post_id}: type={post.content_type!r} "
            f"intent={post.author_intent!r} authority={post.issuing_authority!r}"
        )
    else:
        logger.debug(f"[Chain2] Post {post_id} already chain2-analyzed, skipping Step 2")

    # ── Step 3：作者立场分析（基于近期帖子）──────────────────────────────
    posts_result = await session.exec(
        select(RawPost)
        .where(RawPost.author_platform_id == author.platform_id)
        .where(RawPost.source == author.platform)
        .order_by(RawPost.posted_at.desc())
        .limit(_MAX_POSTS_FOR_STANCE)
    )
    recent_posts = list(posts_result.all())

    stance_parsed: dict | None = None
    if recent_posts:
        stance_parsed = await _analyze_stance(author, recent_posts)
        await _upsert_stance_profile(author.id, stance_parsed, session)
        await session.flush()

    await session.commit()

    logger.info(
        f"[Chain2] Done: author={author.name} "
        f"tier={author.credibility_tier} "
        f"stance={stance_parsed.get('stance_label') if stance_parsed else 'N/A'}"
    )

    return _build_result(post, author, stance_parsed)


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------


async def _analyze_post(post: RawPost, author: Author) -> dict | None:
    """Step 2：内容分类 + 作者意图分析（per-post）。"""
    from anchor.chains.prompts.post_analysis import SYSTEM, build_user_message

    content = post.enriched_content or post.content or ""
    user_msg = build_user_message(
        content=content,
        author_name=author.name,
        author_role=author.role,
        author_expertise=author.expertise_areas,
        content_summary=post.content_summary,
        situation_note=author.situation_note,
    )
    resp = await chat_completion(system=SYSTEM, user=user_msg, max_tokens=_POST_ANALYSIS_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


async def _analyze_stance(author: Author, recent_posts: list[RawPost]) -> dict | None:
    """Step 3：基于近期帖子的作者立场聚合分析。"""
    posts_text = _format_posts(recent_posts)
    prompt = _STANCE_USER_TEMPLATE.format(
        author_name=author.name,
        platform=author.platform,
        role=author.role or "未知",
        expertise=author.expertise_areas or "未知",
        post_count=len(recent_posts),
        posts_text=posts_text,
    )
    resp = await chat_completion(system=_STANCE_SYSTEM, user=prompt, max_tokens=_STANCE_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


# ---------------------------------------------------------------------------
# DB 辅助
# ---------------------------------------------------------------------------


async def _get_or_create_author(post: RawPost, session: AsyncSession) -> Author:
    """根据帖子信息查找或创建 Author 记录。"""
    result = await session.exec(
        select(Author)
        .where(Author.platform == post.source)
        .where(Author.platform_id == (post.author_platform_id or post.author_name))
    )
    author = result.first()
    if author is None:
        author = Author(
            name=post.author_name,
            platform=post.source,
            platform_id=post.author_platform_id or post.author_name,
        )
        session.add(author)
        await session.flush()
        await session.refresh(author)
    return author


async def _upsert_stance_profile(
    author_id: int, parsed: dict | None, session: AsyncSession
) -> None:
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
            dist: dict[str, int] = {}
            if profile.stance_distribution:
                try:
                    dist = json.loads(profile.stance_distribution)
                except Exception:
                    pass
            dist[stance] = dist.get(stance, 0) + 1
            profile.stance_distribution = json.dumps(dist, ensure_ascii=False)
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


# ---------------------------------------------------------------------------
# 格式化 / 解析辅助
# ---------------------------------------------------------------------------


def _format_posts(posts: list[RawPost]) -> str:
    lines: list[str] = []
    for i, post in enumerate(posts, 1):
        content = (post.enriched_content or post.content or "").strip()
        if len(content) > _MAX_POST_CHARS:
            content = content[:_MAX_POST_CHARS] + "…"
        lines.append(f"[{i}] ({post.posted_at.strftime('%Y-%m-%d') if post.posted_at else '?'})\n{content}")
    return "\n\n".join(lines)


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


def _build_result(post: RawPost, author: Author, stance_parsed: dict | None) -> dict:
    return {
        "post_id": post.id,
        "content_type": post.content_type,
        "content_type_secondary": post.content_type_secondary,
        "content_topic": post.content_topic,
        "author_intent": post.author_intent,
        "intent_note": post.intent_note,
        "author_id": author.id,
        "author_name": author.name,
        "role": author.role,
        "credibility_tier": author.credibility_tier,
        "expertise_areas": author.expertise_areas,
        "stance_label": stance_parsed.get("stance_label") if stance_parsed else None,
        "audience": stance_parsed.get("audience") if stance_parsed else None,
        "core_message": stance_parsed.get("core_message") if stance_parsed else None,
        "author_summary": stance_parsed.get("author_summary") if stance_parsed else None,
    }
