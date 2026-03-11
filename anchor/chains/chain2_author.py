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


# ---------------------------------------------------------------------------
# watchlist 机构 → tier 映射（缓存）
# ---------------------------------------------------------------------------

_INSTITUTION_TIER_CACHE: dict[str, int] | None = None


def _load_institution_tier_map() -> dict[str, int]:
    """从 watchlist.yaml 构建 机构关键词 → tier 映射。

    当作者名匹配某机构时，使用 watchlist 中该机构对应的 tier。
    例如 institution="美联储 (Federal Reserve)" → 提取 "美联储"、"Federal Reserve"
    """
    global _INSTITUTION_TIER_CACHE
    if _INSTITUTION_TIER_CACHE is not None:
        return _INSTITUTION_TIER_CACHE

    import yaml
    from pathlib import Path

    mapping: dict[str, int] = {}
    wl_path = Path(__file__).parent.parent.parent / "watchlist.yaml"
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
        # 提取括号内外的关键词，也按逗号、顿号拆分
        # "日本银行 (Bank of Japan, BOJ) 总裁" → ["日本银行", "Bank of Japan", "BOJ"]
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
    """若作者名匹配 watchlist 中某机构，返回该机构的 tier，否则 None。"""
    if not author_name:
        return None
    mapping = _load_institution_tier_map()
    name_lower = author_name.lower()
    for inst_kw, tier in mapping.items():
        if inst_kw.lower() in name_lower or name_lower in inst_kw.lower():
            return tier
    return None
_POST_ANALYSIS_MAX_TOKENS = 800
_MAX_POSTS_FOR_STANCE = 10
_MAX_POST_CHARS = 400

_VALID_CONTENT_TYPES = {
    "财经分析", "市场动向", "产业链研究", "公司调研",
    "技术论文", "政策解读",
}

_VALID_SUBTYPES = {
    "市场分析", "地缘分析", "政策分析", "技术影响", "混合分析",
}

_VALID_INTENTS: set[str] = set()  # 开放式文本，不做枚举校验

_VALID_STANCE_LABELS: set[str] = set()  # 开放式文本，不做枚举校验

# ---------------------------------------------------------------------------
# 立场分析提示词
# ---------------------------------------------------------------------------

_STANCE_SYSTEM = """\
你是一名专业媒体与舆论分析师，擅长从多个维度判断意见领袖的真实立场。
立场是多维度的，需要分别评估以下四个维度：

【意识形态】该作者的政治/经济主张倾向
  示例：自由市场主义 / 凯恩斯主义 / 左翼进步派 / 右翼保守派 / 民族主义 / 民粹主义 / 技术官僚 / 无明显倾向

【地缘立场】该作者在国际关系上倾向于哪一方
  示例：亲美 / 亲中 / 亲俄 / 亲欧 / 反建制 / 多极主义者 / 中立 / 无法判断

【利益代表】该作者的观点背后服务于谁的利益
  示例：独立分析师 / 所在机构（需注明机构名）/ 华尔街 / 中国官方 / 政党利益 / 个人品牌 / 无法判断

【客观性】该作者整体的中立程度
  示例：相对客观 / 有明显倾向 / 立场鲜明 / 宣传口吻

要求：
- 每个维度用≤15字简短描述，无法判断时填"无法判断"
- 不要用市场情绪词（看涨/看跌）来回答任何维度
- 输出必须是合法 JSON，不加任何其他文字\
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
从意识形态、地缘立场、利益代表、客观性四个维度分析该作者的立场。

严格输出 JSON：

```json
{{
  "ideology": "意识形态（≤15字）",
  "geo_stance": "地缘立场（≤15字）",
  "interest_rep": "利益代表（≤20字）",
  "objectivity": "客观性（≤10字）",
  "audience": "目标受众（≤40字）",
  "core_message": "核心信息（≤80字）",
  "author_summary": "综合一句话描述（≤100字）"
}}
```\
"""


# ---------------------------------------------------------------------------
# 公开函数：前置分类（供 Chain 1 调用）
# ---------------------------------------------------------------------------


async def classify_post(
    post: RawPost,
    session: AsyncSession,
    author_hint: str | None = None,
) -> dict:
    """Chain 2 前两步：作者档案 + 内容分类，供 Chain 1 前置调用。

    不含立场分析（Step 3），不依赖 content_summary。
    若 post.chain2_analyzed 已为 True，直接返回 DB 字段，不重复 LLM 调用。
    """
    author = await _get_or_create_author(post, session)
    await AuthorProfiler().profile(author, session)
    # 机构作者 tier 覆盖：watchlist 机构 tier 优先于 LLM 判定
    inst_tier = _lookup_institution_tier(author.name)
    if inst_tier is not None and author.credibility_tier != inst_tier:
        logger.info(
            f"[Chain2] Institution tier override: {author.name!r} "
            f"tier {author.credibility_tier} → {inst_tier}"
        )
        author.credibility_tier = inst_tier
    await session.flush()
    await session.refresh(author)

    if not post.chain2_analyzed:
        post_analysis = await _analyze_post(post, author, author_hint=author_hint)
        if post_analysis:
            ct = post_analysis.get("content_type")
            ct2 = post_analysis.get("content_type_secondary")
            if ct not in _VALID_CONTENT_TYPES:
                logger.warning(f"[Chain2] classify_post: invalid content_type={ct!r}, ignoring")
                ct = None
            if ct2 and ct2 not in _VALID_CONTENT_TYPES:
                ct2 = None
            intent = _safe_str(post_analysis.get("author_intent"))
            post.content_type = ct
            post.content_type_secondary = ct2 if ct2 != ct else None
            post.content_topic = _safe_str(post_analysis.get("content_topic"))
            post.author_intent = intent
            # 财经分析子分类
            subtype = _safe_str(post_analysis.get("content_subtype"))
            post.content_subtype = subtype if subtype in _VALID_SUBTYPES else None
            # ── 作者归因（三级优先：内容/标题 > watchlist hint > 上传者）──
            real_name = _safe_str(post_analysis.get("real_author_name"))
            if not real_name and author_hint:
                real_name = author_hint
                logger.info(f"[Chain2] Using watchlist author hint: {author_hint!r}")
            if real_name and real_name != author.name:
                author = await _reassign_author(post, author, real_name, session)
            # 发文机关（仅政策解读类内容）
            if ct == "政策解读":
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
        "content_subtype": post.content_subtype,
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


async def run_chain2(
    post_id: int,
    session: AsyncSession,
    author_hint: str | None = None,
) -> dict:
    """执行链路2：内容分类 + 作者档案 + 立场分析

    Args:
        post_id:      raw_posts 表主键
        session:      异步数据库 Session
        author_hint:  来自 watchlist 的真实作者姓名（可选，作为兜底参考）

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
    # 机构作者 tier 覆盖：watchlist 机构 tier 优先于 LLM 判定
    inst_tier = _lookup_institution_tier(author.name)
    if inst_tier is not None and author.credibility_tier != inst_tier:
        logger.info(
            f"[Chain2] Institution tier override: {author.name!r} "
            f"tier {author.credibility_tier} → {inst_tier}"
        )
        author.credibility_tier = inst_tier
    await session.flush()
    await session.refresh(author)

    # ── Step 2：内容分类 + 作者意图（per-post）────────────────────────────
    post_analysis: dict | None = None
    if not post.chain2_analyzed:
        post_analysis = await _analyze_post(post, author, author_hint=author_hint)
        if post_analysis:
            ct = post_analysis.get("content_type")
            ct2 = post_analysis.get("content_type_secondary")
            if ct not in _VALID_CONTENT_TYPES:
                logger.warning(f"[Chain2] Invalid content_type={ct!r}, ignoring")
                ct = None
            if ct2 and ct2 not in _VALID_CONTENT_TYPES:
                ct2 = None

            intent = _safe_str(post_analysis.get("author_intent"))
            post.content_type = ct
            post.content_type_secondary = ct2 if ct2 != ct else None
            post.content_topic = _safe_str(post_analysis.get("content_topic"))
            post.author_intent = intent
            # 财经分析子分类
            subtype = _safe_str(post_analysis.get("content_subtype"))
            post.content_subtype = subtype if subtype in _VALID_SUBTYPES else None
            # ── 作者归因（三级优先：内容/标题 > watchlist hint > 上传者）──
            real_name = _safe_str(post_analysis.get("real_author_name"))
            if not real_name and author_hint:
                # LLM 未检测到实际发言人，使用 watchlist 提供的作者名
                real_name = author_hint
                logger.info(f"[Chain2] Using watchlist author hint: {author_hint!r}")
            if real_name and real_name != author.name:
                author = await _reassign_author(post, author, real_name, session)
            # 发文机关（仅政策解读类内容）
            if ct == "政策解读":
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


async def _analyze_post(
    post: RawPost, author: Author, author_hint: str | None = None,
) -> dict | None:
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
        author_hint=author_hint,
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


async def _reassign_author(
    post: RawPost,
    old_author: Author,
    real_name: str,
    session: AsyncSession,
) -> Author:
    """将帖子从共享 Author 重新指向真实作者的独立 Author 记录。

    不修改原 Author（可能被其他帖子共享），而是查找或创建一个
    platform_id=real_name 的新 Author 记录，并更新帖子的关联。
    """
    logger.info(f"[Chain2] Real author identified: {old_author.name!r} → {real_name!r}")

    # 查找是否已有该真实作者的 Author 记录
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
        logger.info(f"[Chain2] Created new Author id={new_author.id} for {real_name!r}")
    else:
        logger.info(f"[Chain2] Found existing Author id={new_author.id} for {real_name!r}")

    # 更新帖子指向新作者
    post.author_name = real_name
    post.author_platform_id = real_name
    session.add(post)

    return new_author


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
        # 四维度拼合成结构化文本存入 dominant_stance
        dims = [
            ("意识形态", parsed.get("ideology")),
            ("地缘立场", parsed.get("geo_stance")),
            ("利益代表", parsed.get("interest_rep")),
            ("客观性",   parsed.get("objectivity")),
        ]
        stance = "\n".join(f"{k}｜{v}" for k, v in dims if v and v != "无法判断") or None

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
        "content_subtype": post.content_subtype,
        "content_type_secondary": post.content_type_secondary,
        "content_topic": post.content_topic,
        "author_intent": post.author_intent,
        "intent_note": post.intent_note,
        "author_id": author.id,
        "author_name": author.name,
        "role": author.role,
        "credibility_tier": author.credibility_tier,
        "expertise_areas": author.expertise_areas,
        "stance_label": stance_parsed.get("ideology") if stance_parsed else None,
        "audience": stance_parsed.get("audience") if stance_parsed else None,
        "core_message": stance_parsed.get("core_message") if stance_parsed else None,
        "author_summary": stance_parsed.get("author_summary") if stance_parsed else None,
    }
