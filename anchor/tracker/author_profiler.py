"""
Layer3 Step 0 — 作者档案分析器
================================
在开始观点验证前，先分析作者的角色、专业背景和可信度：
  - 如果 Author.profile_fetched == True，跳过（已分析过）
  - 通过 Tavily 联网搜索获取作者公开信息（可选，无 Key 时降级）
  - 调用 LLM 生成结构化档案：
      role（职业角色）、expertise_areas（专业领域）、
      known_biases（已知立场偏见）、credibility_tier（1-5分级）、
      profile_note（综合描述，≤80字）

credibility_tier 分级标准：
  1 — 顶级权威：诺贝尔奖得主、央行行长、国际货币基金组织官员等
  2 — 行业专家：知名对冲基金管理人、大型机构首席经济学家、学术权威
  3 — 知名评论员：财经媒体知名主播/记者、有一定从业背景的独立分析师
  4 — 普通媒体/KOL：一般社交媒体账号、无显著专业背景的评论人
  5 — 未知：无法查到任何背景信息

结果写入 Author 的档案字段，并标记 profile_fetched = True。
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Author, _utcnow
from anchor.tracker.web_searcher import format_search_results, web_search

_MAX_TOKENS = 800

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SYSTEM = """\
你是一名公众人物背景分析专家。给定某位公众人物的姓名和平台信息，\
以及可选的网络搜索结果，请判断此人的职业角色、专业背景和可信度层级。

**填写 expertise_areas 时请注意：**
宏观经济、国际关系、地缘政治、财政/货币政策、战略研究、政治经济学是高度连带的领域，\
如果作者在其中任一领域有背景，应将相关的连带领域一并列入 expertise_areas，\
而不是仅列出其"核心"专业。例如宏观经济学家的 expertise_areas 应包含地缘政治风险分析。

credibility_tier 分级：
  1 = 顶级权威（诺贝尔奖得主、央行行长、IMF/BIS官员等）
  2 = 行业专家（知名对冲基金管理人、大型机构首席经济学家、顶尖学者）
  3 = 知名评论员（财经媒体知名主播/记者、有从业背景的独立分析师）
  4 = 普通媒体/KOL（一般社交媒体博主、无显著专业背景的评论人）
  5 = 未知（无法找到任何可信背景信息）

输出必须是合法 JSON，不加任何其他文字。\
"""

# ---------------------------------------------------------------------------
# 有搜索结果版提示
# ---------------------------------------------------------------------------

_PROMPT_WITH_SEARCH = """\
## 分析对象
姓名：{name}
平台：{platform}
平台个人简介：{description}

## 网络搜索结果
{search_results}

## 任务
基于以上信息，分析此人的职业背景，生成结构化档案。

严格输出 JSON：

```json
{{
  "role": "<职业角色，如'桥水基金创始人'、'美联储前主席'、'财经记者'>",
  "expertise_areas": "<专业领域，如'全球宏观经济、债务周期、资本市场'，无则写null>",
  "known_biases": "<已知立场偏见或典型观点倾向，如'长期黄金多头、美元悲观主义'，无则写null>",
  "credibility_tier": <1-5整数>,
  "profile_note": "<综合描述，≤80字，说明此人的专业背景和观点代表性>"
}}
```\
"""

# ---------------------------------------------------------------------------
# 无搜索结果版提示
# ---------------------------------------------------------------------------

_PROMPT_NO_SEARCH = """\
## 分析对象
姓名：{name}
平台：{platform}
平台个人简介：{description}

## 任务
基于你的训练知识，分析此人的职业背景，生成结构化档案。\
若确实无法识别此人，请诚实填写 credibility_tier=5 并在 profile_note 中说明。

严格输出 JSON：

```json
{{
  "role": "<职业角色，如'桥水基金创始人'、'美联储前主席'、'财经记者'，未知则写'未知'>",
  "expertise_areas": "<专业领域，未知则写null>",
  "known_biases": "<已知立场偏见，未知则写null>",
  "credibility_tier": <1-5整数>,
  "profile_note": "<综合描述，≤80字>"
}}
```\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class AuthorProfiler:
    """分析作者角色档案（Layer3 Step 0）。"""

    async def profile(self, author: Author, session: AsyncSession) -> None:
        """查询并写入作者档案。若已查询过则跳过。"""

        if author.profile_fetched:
            logger.debug(f"[AuthorProfiler] author id={author.id} already profiled, skip")
            return

        logger.info(f"[AuthorProfiler] profiling author: {author.name} ({author.platform})")

        # ── 联网搜索（可选）──────────────────────────────────────────────────
        search_results = await web_search(
            query=f"{author.name} {author.platform} background role expertise biography",
            max_results=5,
        )

        # ── 构建 prompt ───────────────────────────────────────────────────────
        description = author.description or "（无平台简介）"

        if search_results:
            search_text = format_search_results(search_results)
            prompt = _PROMPT_WITH_SEARCH.format(
                name=author.name,
                platform=author.platform,
                description=description,
                search_results=search_text,
            )
            logger.debug(f"[AuthorProfiler] using web search results ({len(search_results)} results)")
        else:
            prompt = _PROMPT_NO_SEARCH.format(
                name=author.name,
                platform=author.platform,
                description=description,
            )
            logger.debug("[AuthorProfiler] no web search results, using training knowledge only")

        # ── 调用 LLM ──────────────────────────────────────────────────────────
        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[AuthorProfiler] LLM call failed for author id={author.id}")
            _mark_fetched(author)
            session.add(author)
            await session.flush()
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[AuthorProfiler] JSON parse failed for author id={author.id}")
            _mark_fetched(author)
            session.add(author)
            await session.flush()
            return

        # ── 写入档案字段 ──────────────────────────────────────────────────────
        author.role = _to_str(parsed.get("role"))
        author.expertise_areas = _to_str(parsed.get("expertise_areas"))
        author.known_biases = _to_str(parsed.get("known_biases"))
        author.profile_note = _to_str(parsed.get("profile_note"))

        tier = parsed.get("credibility_tier")
        if isinstance(tier, int) and 1 <= tier <= 5:
            author.credibility_tier = tier
        else:
            author.credibility_tier = 5  # 默认未知

        _mark_fetched(author)
        session.add(author)
        await session.flush()

        logger.info(
            f"[AuthorProfiler] author id={author.id} | role={author.role} | "
            f"tier={author.credibility_tier} | {author.profile_note}"
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_str(value) -> str | None:
    """将 LLM 返回的值强制转为字符串（LLM 可能返回 list 而非 str）。"""
    if value is None:
        return None
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    s = str(value).strip()
    return s if s and s.lower() not in ("null", "none", "未知") else None


def _mark_fetched(author: Author) -> None:
    author.profile_fetched = True
    author.profile_fetched_at = _utcnow()


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
        logger.warning(f"[AuthorProfiler] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
