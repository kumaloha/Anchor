"""
Layer1 Step A — 跨平台作者实体识别
======================================
当新 Author 被创建时，判断其是否与已存在的 Author 是同一真实人物。
若是（confidence >= 0.80），则为两者建立共享的 AuthorGroup。

候选人筛选（减少 LLM 调用）：
  - DB 中 Author.name 含新作者名称的首个词（LIKE 模糊匹配）
  - 且 Author.platform != new_author.platform（必须是不同平台）
  - 最多取 5 个候选人

LLM 判断输出格式：
  {
    "matched": true,
    "matched_author_id": 42,
    "confidence": 0.92,
    "reason": "同名且职业描述一致（桥水基金创始人）"
  }

合并逻辑：
  - matched_author 已有 author_group_id：新 Author 加入同一 group
  - matched_author 无 author_group_id：创建新 AuthorGroup，两者都指向它
"""

from __future__ import annotations

import json

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Author, AuthorGroup, _utcnow

_SYSTEM = """你是一个跨平台作者身份识别系统。
给定一位新作者的信息和一组候选作者列表，判断新作者是否与某个候选者是同一真实人物。
不同平台上的同名账号可能是同一人，但需要结合角色描述等信息综合判断。
仅输出 JSON，不要任何其他内容。"""


class AuthorGroupMatcher:
    """Layer1 Step A：创建新 Author 后，尝试关联跨平台同名实体。"""

    async def match(self, new_author: Author, session: AsyncSession) -> None:
        """检查 new_author 是否与已有 Author 是同一人，若是则建立 AuthorGroup 关联。"""
        try:
            candidates = await self._find_candidates(new_author, session)
            if not candidates:
                return

            result = await self._call_llm(new_author, candidates)
            if result is None:
                return

            if not result.get("matched") or result.get("confidence", 0) < 0.80:
                logger.debug(
                    f"[AuthorGroupMatcher] No match for author id={new_author.id} "
                    f"({new_author.name}/{new_author.platform}), "
                    f"confidence={result.get('confidence', 0):.2f}"
                )
                return

            matched_id = result.get("matched_author_id")
            if not matched_id:
                return

            matched_author = await session.get(Author, matched_id)
            if matched_author is None:
                return

            # 决定 author_group_id：复用已有 group 或新建
            if matched_author.author_group_id:
                group_id = matched_author.author_group_id
            else:
                group = AuthorGroup(
                    canonical_name=new_author.name,
                    canonical_role=new_author.role or matched_author.role,
                )
                session.add(group)
                await session.flush()
                group_id = group.id
                matched_author.author_group_id = group_id
                session.add(matched_author)

            new_author.author_group_id = group_id
            session.add(new_author)

            logger.info(
                f"[AuthorGroupMatcher] Linked author id={new_author.id} "
                f"({new_author.name}/{new_author.platform}) "
                f"→ group_id={group_id} via matched id={matched_id} "
                f"confidence={result.get('confidence', 0):.2f} | {result.get('reason', '')}"
            )

        except Exception as exc:
            logger.warning(f"[AuthorGroupMatcher] Error during matching: {exc}")

    async def _find_candidates(self, new_author: Author, session: AsyncSession) -> list[Author]:
        """查找候选：相似名称 + 不同平台，最多 5 个。"""
        name_parts = (new_author.name or "").split()
        if not name_parts:
            return []
        # 取名字首词作为关键词（对"Ray Dalio"取"Ray"，对"刘强东"取"刘强东"）
        keyword = name_parts[0]
        if len(keyword) < 2:
            return []

        result = await session.exec(
            select(Author).where(
                Author.name.contains(keyword),
                Author.platform != new_author.platform,
                Author.id != (new_author.id or -1),
            ).limit(5)
        )
        return list(result.all())

    async def _call_llm(self, new_author: Author, candidates: list[Author]) -> dict | None:
        candidates_info = [
            {
                "id": c.id,
                "name": c.name,
                "platform": c.platform,
                "role": c.role,
                "description": c.description,
            }
            for c in candidates
        ]
        user = (
            f"新作者：name={new_author.name!r}, platform={new_author.platform!r}, "
            f"role={new_author.role!r}, description={new_author.description!r}\n\n"
            f"候选者列表：\n{json.dumps(candidates_info, ensure_ascii=False, indent=2)}\n\n"
            f"判断新作者是否与某个候选者是同一真实人物（至少 confidence=0.80 才认为是同一人）。\n"
            f"输出格式：\n"
            f'{{"matched": true/false, "matched_author_id": <int或null>, '
            f'"confidence": <0.0-1.0>, "reason": "<简短说明>"}}'
        )

        resp = await chat_completion(system=_SYSTEM, user=user, max_tokens=200)
        if resp is None:
            return None

        try:
            raw = resp.content.strip()
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(raw[start:end])
        except Exception as exc:
            logger.warning(f"[AuthorGroupMatcher] Failed to parse LLM response: {exc}")
            return None
