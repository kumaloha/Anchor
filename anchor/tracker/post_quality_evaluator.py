"""
Layer3 Step 8 — 内容质量评估器
================================
对每篇已处理的原始帖子（RawPost）进行质量评估：

  独特性（Uniqueness）：
    - 提取该帖子中的 canonical_claims（Facts + Conclusions）
    - 在数据库中搜索其他作者是否有相同 canonical_claim
    - 计算 similar_claim_count, similar_author_count
    - 判断 is_first_mover（此帖 posted_at 是否早于所有相似帖）
    - uniqueness_score = 1 / (1 + 0.4 * similar_author_count)

  有效性（Effectiveness）：
    - 将原始帖子内容传给 LLM
    - LLM 评估实质内容比例 vs 噪声比例
    - 噪声类型：emotional_rhetoric / entertainment / filler
    - 返回 effectiveness_score, noise_ratio, noise_types, effectiveness_note

结果写入 PostQualityAssessment。
若记录已存在则跳过。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Author,
    Conclusion,
    Fact,
    PostQualityAssessment,
    RawPost,
)

_MAX_TOKENS = 512

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SYSTEM = """\
你是一名内容质量分析专家，专注于金融/经济评论领域。
你的任务是评估一篇内容的信息有效性——实质性专业内容与噪声内容的比例。

噪声类型定义：
  emotional_rhetoric — 情绪化表达，渲染恐惧/愤怒/兴奋但无实质信息（如"这是灾难！"）
  entertainment      — 与专业内容无关的娱乐性插话、个人轶事、与主题无关的笑话等
  filler             — 反复重申相同观点、套话、无新信息的废话（如绕来绕去表达同一个意思）

注意：正常的论证展开、举例说明、引用数据都是实质性内容，不算噪声。
输出必须是合法 JSON，不加任何其他文字。\
"""

_PROMPT = """\
## 待评估内容

{content}

## 评估任务

请分析上述内容的信息有效性。

严格输出 JSON：

```json
{{
  "effectiveness_score": <0.0-1.0，实质内容占比，1.0=全是实质内容，0.0=全是噪声>,
  "noise_ratio": <0.0-1.0，噪声占比>,
  "noise_types": [<存在的噪声类型，从 emotional_rhetoric/entertainment/filler 中选，无则为空数组>],
  "effectiveness_note": "<1句话说明，≤60字，指出主要问题或亮点>"
}}
```\
"""


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class PostQualityEvaluator:
    """评估单篇内容的独特性和有效性（Layer3 Step 8）。"""

    async def assess(
        self,
        raw_post: RawPost,
        author: Author,
        session: AsyncSession,
    ) -> None:
        """评估 raw_post 的质量，写入 PostQualityAssessment。若已评估则跳过。"""

        # ── 检查是否已评估 ────────────────────────────────────────────────────
        existing_r = await session.exec(
            select(PostQualityAssessment).where(
                PostQualityAssessment.raw_post_id == raw_post.id
            )
        )
        if existing_r.first() is not None:
            logger.debug(
                f"[PostQualityEvaluator] raw_post id={raw_post.id} already assessed, skip"
            )
            return

        logger.info(
            f"[PostQualityEvaluator] assessing raw_post id={raw_post.id} "
            f"author={author.name}"
        )

        # ── 独特性分析 ────────────────────────────────────────────────────────
        (
            uniqueness_score,
            uniqueness_note,
            is_first_mover,
            similar_claim_count,
            similar_author_count,
        ) = await self._assess_uniqueness(raw_post, author, session)

        # ── 有效性分析 ────────────────────────────────────────────────────────
        (
            effectiveness_score,
            effectiveness_note,
            noise_ratio,
            noise_types,
        ) = await self._assess_effectiveness(raw_post)

        # ── 写入数据库 ────────────────────────────────────────────────────────
        assessment = PostQualityAssessment(
            raw_post_id=raw_post.id,
            author_id=author.id,
            uniqueness_score=uniqueness_score,
            uniqueness_note=uniqueness_note,
            is_first_mover=is_first_mover,
            similar_claim_count=similar_claim_count,
            similar_author_count=similar_author_count,
            effectiveness_score=effectiveness_score,
            effectiveness_note=effectiveness_note,
            noise_ratio=noise_ratio,
            noise_types=(
                json.dumps(noise_types, ensure_ascii=False) if noise_types else None
            ),
        )
        session.add(assessment)
        await session.flush()

        logger.info(
            f"[PostQualityEvaluator] raw_post id={raw_post.id} | "
            f"uniqueness={uniqueness_score:.2f} first_mover={is_first_mover} | "
            f"effectiveness={effectiveness_score:.2f} noise={noise_ratio:.2f}"
        )

    # ── 独特性分析 ─────────────────────────────────────────────────────────────

    async def _assess_uniqueness(
        self,
        raw_post: RawPost,
        author: Author,
        session: AsyncSession,
    ) -> tuple[float, str | None, bool, int, int]:
        """
        返回 (uniqueness_score, uniqueness_note, is_first_mover,
               similar_claim_count, similar_author_count)
        """

        # ── 收集本帖的 canonical_claims ───────────────────────────────────────
        post_canonical_claims: list[str] = []

        # 1. 来自 Facts（通过 raw_post_id）
        fact_r = await session.exec(
            select(Fact).where(
                Fact.raw_post_id == raw_post.id,
                Fact.canonical_claim != None,  # noqa: E711
            )
        )
        for fact in fact_r.all():
            if fact.canonical_claim:
                post_canonical_claims.append(fact.canonical_claim)

        # 2. 来自 Conclusions（通过 source_url + author_id）
        conc_r = await session.exec(
            select(Conclusion).where(
                Conclusion.source_url == raw_post.url,
                Conclusion.author_id == author.id,
                Conclusion.canonical_claim != None,  # noqa: E711
            )
        )
        for conc in conc_r.all():
            if conc.canonical_claim:
                post_canonical_claims.append(conc.canonical_claim)

        unique_claims = list(dict.fromkeys(post_canonical_claims))  # 去重保序

        if not unique_claims:
            return 0.5, "未提取到规范化观点，无法评估独特性", False, 0, 0

        logger.debug(
            f"[PostQualityEvaluator] post id={raw_post.id} "
            f"has {len(unique_claims)} canonical_claims"
        )

        # ── 在 DB 中查找相似观点 ──────────────────────────────────────────────
        total_similar = 0
        other_author_keys: set = set()   # platform_id 或 author_name
        earliest_other: Optional[object] = None  # 最早的相似帖 posted_at

        for claim in unique_claims:
            # Facts 中的相同 canonical_claim（来自其他帖子）
            sf_r = await session.exec(
                select(Fact).where(
                    Fact.canonical_claim == claim,
                    Fact.raw_post_id != raw_post.id,
                )
            )
            for sf in sf_r.all():
                if sf.raw_post_id is None:
                    continue
                rp_r = await session.exec(
                    select(RawPost).where(RawPost.id == sf.raw_post_id)
                )
                rp = rp_r.first()
                if rp is None:
                    continue
                # 排除同一作者的其他帖子
                if rp.author_platform_id == raw_post.author_platform_id:
                    continue
                total_similar += 1
                other_author_keys.add(
                    rp.author_platform_id or rp.author_name
                )
                if earliest_other is None or rp.posted_at < earliest_other:
                    earliest_other = rp.posted_at

            # Conclusions 中的相同 canonical_claim（来自其他作者）
            sc_r = await session.exec(
                select(Conclusion).where(
                    Conclusion.canonical_claim == claim,
                    Conclusion.author_id != author.id,
                )
            )
            for sc in sc_r.all():
                total_similar += 1
                other_author_keys.add(sc.author_id)
                if earliest_other is None or sc.posted_at < earliest_other:
                    earliest_other = sc.posted_at

        similar_claim_count = total_similar
        similar_author_count = len(other_author_keys)

        # ── 判断是否是先行者 ──────────────────────────────────────────────────
        is_first_mover = (
            earliest_other is None  # 没有其他人说过
            or raw_post.posted_at <= earliest_other
        )

        # ── 计算独特性分数 ────────────────────────────────────────────────────
        # 公式：1 / (1 + 0.4 * similar_author_count)
        # 0 人 → 1.00；1 人 → 0.71；3 人 → 0.45；10 人 → 0.20
        uniqueness_score = 1.0 / (1.0 + 0.4 * similar_author_count)

        if similar_author_count == 0:
            uniqueness_note = "此类观点在数据库中尚无其他作者表达，具有独特性"
        elif is_first_mover:
            uniqueness_note = (
                f"有 {similar_author_count} 位其他作者表达了相似观点，"
                f"但当前作者是最早提出者"
            )
        else:
            uniqueness_note = f"已有 {similar_author_count} 位其他作者表达了相似观点"

        return (
            uniqueness_score,
            uniqueness_note,
            is_first_mover,
            similar_claim_count,
            similar_author_count,
        )

    # ── 有效性分析 ─────────────────────────────────────────────────────────────

    async def _assess_effectiveness(
        self,
        raw_post: RawPost,
    ) -> tuple[float, str | None, float, list[str]]:
        """
        返回 (effectiveness_score, effectiveness_note, noise_ratio, noise_types)
        """
        content = raw_post.enriched_content or raw_post.content
        if not content or len(content.strip()) < 50:
            return 0.5, "内容过短，无法有效评估", 0.5, []

        # 截断超长内容
        if len(content) > 3000:
            content = content[:3000] + "...（已截断）"

        prompt = _PROMPT.format(content=content)
        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )

        if resp is None:
            logger.warning(
                f"[PostQualityEvaluator] LLM call failed for raw_post id={raw_post.id}"
            )
            return 0.5, None, 0.5, []

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(
                f"[PostQualityEvaluator] JSON parse failed for raw_post id={raw_post.id}"
            )
            return 0.5, None, 0.5, []

        effectiveness_score = _clamp(float(parsed.get("effectiveness_score") or 0.5))
        noise_ratio = _clamp(float(parsed.get("noise_ratio") or 0.5))

        noise_types = parsed.get("noise_types") or []
        if isinstance(noise_types, str):
            noise_types = [noise_types]

        effectiveness_note = parsed.get("effectiveness_note") or None

        return effectiveness_score, effectiveness_note, noise_ratio, noise_types


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


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
        logger.warning(
            f"[PostQualityEvaluator] JSON parse error: {exc}\nRaw: {raw[:300]}"
        )
        return None
