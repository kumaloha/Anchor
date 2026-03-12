"""
通用判断 — General Assessment
==============================
输入：raw_post_id（主）→ 自动关联 author
输出：写入 DB 的 Author 档案 + RawPost 内容分类（2D）+ 利益冲突 + 摘要

两步判断：
  1. 作者背景  AuthorProfiler → Author 档案（role / expertise / credibility_tier）
  2. 文章分类  content_domain / content_nature / assessment_summary / has_conflict + 过渡兼容 content_type

用法：
  async with AsyncSessionLocal() as session:
      result = await run_assessment(post_id=1, session=session)
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Author, RawPost, _utcnow
from anchor.verify.author_profiler import AuthorProfiler


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_POST_ANALYSIS_MAX_TOKENS = 800

_VALID_CONTENT_TYPES = {
    "财经分析", "市场动向", "产业链研究", "公司调研",
    "技术论文", "公司财报", "政策解读",
}

_VALID_DOMAINS = {"政策", "产业", "公司", "期货", "技术"}
_VALID_NATURES = {"一手信息", "第三方分析"}


# ---------------------------------------------------------------------------
# sources 机构 → tier 映射（缓存）
# ---------------------------------------------------------------------------

_INSTITUTION_TIER_CACHE: dict[str, int] | None = None


def _load_institution_tier_map() -> dict[str, int]:
    """从 sources.yaml 构建 机构关键词 → tier 映射。"""
    global _INSTITUTION_TIER_CACHE
    if _INSTITUTION_TIER_CACHE is not None:
        return _INSTITUTION_TIER_CACHE

    import yaml
    from pathlib import Path

    mapping: dict[str, int] = {}
    wl_path = Path(__file__).parent.parent.parent / "sources.yaml"
    if not wl_path.exists():
        _INSTITUTION_TIER_CACHE = mapping
        return mapping

    try:
        with open(wl_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        _INSTITUTION_TIER_CACHE = mapping
        return mapping

    _title_re = re.compile(
        r"\s*(CEO|COO|CIO|主席|行长|总裁|创始人|联合创始人|院长|教授|所长|"
        r"首席|董事长|总经理|官员|高级研究员|资深记者|评论员).*$"
    )
    _junk_re = re.compile(
        r'^前|^已退休|^独立|诺贝尔|^AUM|^\d{4}\s|^[「\u201c"]'
    )

    for author in data.get("authors", []):
        inst = author.get("institution", "")
        tier = author.get("tier")
        if not inst or not isinstance(tier, int):
            continue
        parts = re.split(r"[()（）/,、]", inst)
        for part in parts:
            kw = _title_re.sub("", part.strip()).strip()
            if len(kw) < 2 or _junk_re.search(kw):
                continue
            if kw not in mapping or tier < mapping[kw]:
                mapping[kw] = tier

    _INSTITUTION_TIER_CACHE = mapping
    return mapping


def _lookup_institution_tier(author_name: str) -> int | None:
    """若作者名匹配 sources.yaml 中某机构，返回该机构的 tier，否则 None。"""
    if not author_name:
        return None
    mapping = _load_institution_tier_map()
    name_lower = author_name.lower()
    for inst_kw, tier in mapping.items():
        if inst_kw.lower() in name_lower or name_lower in inst_kw.lower():
            return tier
    return None


# ---------------------------------------------------------------------------
# 内容路由函数（公开，供所有调用点共享）
# ---------------------------------------------------------------------------


def resolve_content_mode(
    domain: str | None,
    nature: str | None,
    content_type: str | None = None,
) -> str:
    """2D（domain × nature）→ content_mode（v8 统一路由）。

    路由规则：
      政策 + 一手信息 → policy
      任意 + 第三方分析 → expert
      产业 + 一手信息 → industry
      技术 + 一手信息 → technology
      期货 + 一手信息 → futures
      公司 + 一手信息 → company
    """
    if domain and nature:
        if nature == "第三方分析":
            return "expert"
        # 一手信息：取主领域（逗号分隔的第一个）
        primary = domain.split(",")[0].strip()
        if primary == "政策":
            return "policy"
        if primary == "产业":
            return "industry"
        if primary == "技术":
            return "technology"
        if primary == "期货":
            return "futures"
        if domain == "公司":
            return "company"
        return "expert"
    # 旧数据降级
    if content_type in {"政策宣布", "政策解读"}:
        return "policy"
    if content_type in {"产业链研究"}:
        return "industry"
    if content_type == "技术论文":
        return "technology"
    if content_type == "公司财报":
        return "company"
    return "expert"


# ---------------------------------------------------------------------------
# 公开函数：前置评估（供 content_extraction 调用）
# ---------------------------------------------------------------------------


async def assess_post(
    post: RawPost,
    session: AsyncSession,
    author_hint: str | None = None,
) -> dict:
    """通用判断前置：作者背景 + 文章分类 + 摘要 + 利益冲突，供 content_extraction 前置调用。

    若 post.assessed 已为 True，直接返回 DB 字段，不重复 LLM 调用。
    """
    author = await _get_or_create_author(post, session)
    await AuthorProfiler().profile(author, session)
    # 机构作者 tier 覆盖：sources.yaml 机构 tier 优先于 LLM 判定
    inst_tier = _lookup_institution_tier(author.name)
    if inst_tier is not None and author.credibility_tier != inst_tier:
        logger.info(
            f"[Assessment] Institution tier override: {author.name!r} "
            f"tier {author.credibility_tier} → {inst_tier}"
        )
        author.credibility_tier = inst_tier
    await session.flush()
    await session.refresh(author)

    if not post.assessed:
        post_analysis = await _analyze_post(post, author, author_hint=author_hint)
        if post_analysis:
            _apply_analysis(post, author, post_analysis, author_hint, session)
            # 作者归因
            real_name = _safe_str(post_analysis.get("real_author_name"))
            if not real_name and author_hint:
                real_name = author_hint
                logger.info(f"[Assessment] Using sources author hint: {author_hint!r}")
            if real_name and real_name != author.name:
                author = await _reassign_author(post, author, real_name, session)
        post.assessed = True
        post.assessed_at = _utcnow()
        session.add(post)
        await session.flush()
        logger.info(
            f"[Assessment] classify_post {post.id}: domain={post.content_domain!r} "
            f"nature={post.content_nature!r} type={post.content_type!r} "
            f"summary={post.assessment_summary!r}"
        )
    else:
        logger.debug(f"[Assessment] classify_post: post {post.id} already analyzed, returning cached fields")

    return _build_result(post, author)


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def run_assessment(
    post_id: int,
    session: AsyncSession,
    author_hint: str | None = None,
) -> dict:
    """通用判断：作者背景 + 文章分类 + 摘要 + 利益冲突

    Args:
        post_id:      raw_posts 表主键
        session:      异步数据库 Session
        author_hint:  来自 sources.yaml 的真实作者姓名（可选，作为兜底参考）

    Returns:
        dict with keys:
          post_id, content_domain, content_nature, content_type,
          content_topic, assessment_summary, has_conflict, conflict_note,
          author_id, author_name, role, credibility_tier
    """
    # ── 加载帖子 ──────────────────────────────────────────────────────────
    post = await session.get(RawPost, post_id)
    if not post:
        raise ValueError(f"RawPost id={post_id} not found")

    logger.info(f"[Assessment] Analyzing post id={post_id} (author={post.author_name})")

    # ── 加载/创建作者 ─────────────────────────────────────────────────────
    author = await _get_or_create_author(post, session)

    # ── Step 1：作者档案分析 ──────────────────────────────────────────────
    await AuthorProfiler().profile(author, session)
    # 机构作者 tier 覆盖
    inst_tier = _lookup_institution_tier(author.name)
    if inst_tier is not None and author.credibility_tier != inst_tier:
        logger.info(
            f"[Assessment] Institution tier override: {author.name!r} "
            f"tier {author.credibility_tier} → {inst_tier}"
        )
        author.credibility_tier = inst_tier
    await session.flush()
    await session.refresh(author)

    # ── Step 2：内容分类 + 摘要 + 利益冲突（per-post）────────────────────
    if not post.assessed:
        post_analysis = await _analyze_post(post, author, author_hint=author_hint)
        if post_analysis:
            _apply_analysis(post, author, post_analysis, author_hint, session)
            # 作者归因
            real_name = _safe_str(post_analysis.get("real_author_name"))
            if not real_name and author_hint:
                real_name = author_hint
                logger.info(f"[Assessment] Using sources author hint: {author_hint!r}")
            if real_name and real_name != author.name:
                author = await _reassign_author(post, author, real_name, session)

        post.assessed = True
        post.assessed_at = _utcnow()
        session.add(post)
        await session.flush()
        logger.info(
            f"[Assessment] Post {post_id}: domain={post.content_domain!r} "
            f"nature={post.content_nature!r} type={post.content_type!r} "
            f"summary={post.assessment_summary!r}"
        )
    else:
        logger.debug(f"[Assessment] Post {post_id} already already assessed, skipping")

    await session.commit()

    logger.info(
        f"[Assessment] Done: author={author.name} "
        f"tier={author.credibility_tier} "
        f"domain={post.content_domain} nature={post.content_nature}"
    )

    return _build_result(post, author)


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------


async def _analyze_post(
    post: RawPost, author: Author, author_hint: str | None = None,
) -> dict | None:
    """Step 2：内容分类 + 摘要 + 利益冲突分析（per-post）。"""
    from anchor.chains.prompts.post_analysis import SYSTEM, build_user_message

    content = post.enriched_content or post.content or ""
    user_msg = build_user_message(
        content=content,
        author_name=author.name,
        author_role=author.role,
        author_expertise=author.expertise_areas,
        content_summary=post.content_summary,
        situation_note=author.situation_note,
        author_hint=author_hint,
    )
    resp = await chat_completion(system=SYSTEM, user=user_msg, max_tokens=_POST_ANALYSIS_MAX_TOKENS)
    if resp is None:
        return None
    return _parse_json(resp.content)


# ---------------------------------------------------------------------------
# 分析结果写入
# ---------------------------------------------------------------------------


def _apply_analysis(
    post: RawPost,
    author: Author,
    post_analysis: dict,
    author_hint: str | None,
    session: AsyncSession,
) -> None:
    """将 LLM 分析结果写入 RawPost 字段。"""
    # 新字段：2D 分类（domain 支持多值逗号分隔，如 "政策,期货"）
    domain_raw = _safe_str(post_analysis.get("content_domain"))
    nature = _safe_str(post_analysis.get("content_nature"))
    if domain_raw:
        # 验证每个 domain 都合法，过滤非法值
        parts = [d.strip() for d in domain_raw.split(",") if d.strip() in _VALID_DOMAINS]
        post.content_domain = ",".join(parts) if parts else None
    else:
        post.content_domain = None
    post.content_nature = nature if nature in _VALID_NATURES else None

    # 利益冲突
    has_conflict = post_analysis.get("has_conflict")
    if isinstance(has_conflict, bool):
        post.has_conflict = has_conflict
    post.conflict_note = _safe_str(post_analysis.get("conflict_risk"))

    # 摘要
    post.assessment_summary = _safe_str(post_analysis.get("assessment_summary"))

    # 过渡兼容：content_type
    ct = post_analysis.get("content_type")
    ct2 = post_analysis.get("content_type_secondary")
    if ct not in _VALID_CONTENT_TYPES:
        logger.warning(f"[Assessment] Invalid content_type={ct!r}, ignoring")
        ct = None
    if ct2 and ct2 not in _VALID_CONTENT_TYPES:
        ct2 = None
    post.content_type = ct
    post.content_type_secondary = ct2 if ct2 != ct else None
    post.content_topic = _safe_str(post_analysis.get("content_topic"))
    # 写旧 author_intent 字段为 assessment_summary（过渡兼容）
    post.author_intent = post.assessment_summary

    # 发文机关（仅政策类内容）
    if (post.content_domain and "政策" in post.content_domain) or ct == "政策解读":
        ia = _safe_str(post_analysis.get("issuing_authority"))
        al = _safe_str(post_analysis.get("authority_level"))
        if ia:
            post.issuing_authority = ia
        if al:
            post.authority_level = al


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


async def _reassign_author(
    post: RawPost,
    old_author: Author,
    real_name: str,
    session: AsyncSession,
) -> Author:
    """将帖子从共享 Author 重新指向真实作者的独立 Author 记录。"""
    logger.info(f"[Assessment] Real author identified: {old_author.name!r} → {real_name!r}")

    result = await session.exec(
        select(Author)
        .where(Author.platform == post.source)
        .where(Author.platform_id == real_name)
    )
    new_author = result.first()

    if new_author is None:
        new_author = Author(
            name=real_name,
            platform=post.source,
            platform_id=real_name,
        )
        session.add(new_author)
        await session.flush()
        await session.refresh(new_author)
        logger.info(f"[Assessment] Created new Author id={new_author.id} for {real_name!r}")
    else:
        logger.info(f"[Assessment] Found existing Author id={new_author.id} for {real_name!r}")

    post.author_name = real_name
    post.author_platform_id = real_name
    session.add(post)

    return new_author


# ---------------------------------------------------------------------------
# 格式化 / 解析辅助
# ---------------------------------------------------------------------------


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
        logger.warning(f"[Assessment] JSON parse error: {exc}")
        return None


def _safe_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _build_result(post: RawPost, author: Author) -> dict:
    return {
        "post_id": post.id,
        "content_domain": post.content_domain,
        "content_nature": post.content_nature,
        "content_type": post.content_type,
        "content_type_secondary": post.content_type_secondary,
        "content_topic": post.content_topic,
        "assessment_summary": post.assessment_summary,
        "has_conflict": post.has_conflict,
        "conflict_note": post.conflict_note,
        "author_intent": post.author_intent,
        "issuing_authority": post.issuing_authority,
        "authority_level": post.authority_level,
        "author_id": author.id,
        "author_name": author.name,
        "role": author.role,
        "credibility_tier": author.credibility_tier,
        "expertise_areas": author.expertise_areas,
    }
