"""
Chain 2 测试脚本 — 对 anchor_v4_test.db 中已有帖子运行链路2
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./anchor_v4_test.db")


async def main():
    from anchor.database.session import create_tables, AsyncSessionLocal
    from anchor.chains.chain2_author import run_chain2
    from anchor.models import RawPost
    from sqlmodel import select

    await create_tables()

    async with AsyncSessionLocal() as session:
        posts = list((await session.exec(select(RawPost))).all())

    if not posts:
        print("No posts found. Run run_halo_test.py first.")
        return

    print(f"Found {len(posts)} post(s). Running Chain 2...\n")

    for post in posts:
        async with AsyncSessionLocal() as session:
            result = await run_chain2(post_id=post.id, session=session)

        print(f"=== Post {result['post_id']} ===")
        print(f"内容类型   : {result['content_type']}", end="")
        if result['content_type_secondary']:
            print(f"  +  {result['content_type_secondary']}", end="")
        print()
        print(f"具体主题   : {result['content_topic']}")
        print(f"作者意图   : {result['author_intent']}")
        print(f"意图说明   : {result['intent_note']}")
        print(f"---")
        print(f"作者       : {result['author_name']}  ({result['role'] or '未知'})")
        print(f"可信度     : Tier {result['credibility_tier']}")
        print(f"专业背景   : {result['expertise_areas']}")
        print(f"立场标签   : {result['stance_label']}")
        print(f"目标受众   : {result['audience']}")
        print(f"核心信息   : {result['core_message']}")
        print(f"作者综述   : {result['author_summary']}")
        print()


asyncio.run(main())
