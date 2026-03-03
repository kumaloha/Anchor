"""
Layer3 Step 9b — 作者立场分布档案更新
==========================================
在 AuthorStatsUpdater（Step 9）之后执行。
汇总该作者所有 PostQualityAssessment.stance_label，写入/更新 AuthorStanceProfile。

立场分布统计逻辑：
  - 收集该 author_id 的所有 PostQualityAssessment.stance_label（非空）
  - 用 Counter 统计每个立场出现的次数
  - dominant_stance = 出现最多的立场
  - dominant_stance_ratio = 最多次数 / 总数
  - Upsert AuthorStanceProfile
"""

from __future__ import annotations

import json
from collections import Counter

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import Author, AuthorStanceProfile, PostQualityAssessment, _utcnow


class AuthorStanceUpdater:
    """Layer3 Step 9b：更新作者立场分布档案。"""

    async def update(self, author: Author, session: AsyncSession) -> None:
        """汇总该作者所有帖子的立场标签，upsert AuthorStanceProfile。"""
        pqa_r = await session.exec(
            select(PostQualityAssessment).where(
                PostQualityAssessment.author_id == author.id
            )
        )
        assessments = list(pqa_r.all())

        stances = [a.stance_label for a in assessments if a.stance_label]
        if not stances:
            logger.debug(
                f"[AuthorStanceUpdater] No stance labels for author id={author.id}, skipping"
            )
            return

        distribution = dict(Counter(stances))
        total = len(stances)

        top = Counter(stances).most_common(1)[0]
        dominant_stance = top[0]
        dominant_ratio = top[1] / total

        # Upsert
        sp_r = await session.exec(
            select(AuthorStanceProfile).where(
                AuthorStanceProfile.author_id == author.id
            )
        )
        profile = sp_r.first()
        if profile is None:
            profile = AuthorStanceProfile(author_id=author.id)

        profile.stance_distribution = json.dumps(distribution, ensure_ascii=False)
        profile.dominant_stance = dominant_stance
        profile.dominant_stance_ratio = dominant_ratio
        profile.total_analyzed = total
        profile.last_updated = _utcnow()

        session.add(profile)
        await session.flush()

        logger.info(
            f"[AuthorStanceUpdater] author id={author.id} | "
            f"dominant={dominant_stance!r} ({dominant_ratio:.0%}) | "
            f"total={total} | distribution={distribution}"
        )
