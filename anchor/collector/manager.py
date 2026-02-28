"""采集管理器

负责：
1. 初始化并注册所有启用的采集器
2. 将采集结果去重后写入数据库
3. 可选：通过 APScheduler 定时调度
"""

from __future__ import annotations

import json
import argparse
import asyncio
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.collector.base import BaseCollector, RawPostData
from anchor.collector.rss import RSSCollector
from anchor.config import settings
from anchor.database.models import RawPost
from anchor.database.session import AsyncSessionLocal, create_tables


class CollectorManager:
    def __init__(self) -> None:
        self._collectors: list[BaseCollector] = []
        self._register_collectors()

    def _register_collectors(self) -> None:
        # RSS 采集器（无需 API Key，默认启用）
        self._collectors.append(RSSCollector())
        logger.info("Registered collector: RSSCollector")

        # Twitter 采集器（需要 Bearer Token）
        if settings.twitter_bearer_token:
            from anchor.collector.twitter import TwitterCollector
            self._collectors.append(TwitterCollector())
            logger.info("Registered collector: TwitterCollector")
        else:
            logger.warning("TWITTER_BEARER_TOKEN not set — TwitterCollector disabled")

        # 微博采集器
        from anchor.collector.weibo import WeiboCollector
        self._collectors.append(WeiboCollector())
        mode = "API mode" if settings.weibo_access_token else "scraper mode"
        logger.info(f"Registered collector: WeiboCollector ({mode})")

    async def run_once(self) -> int:
        """执行一轮全量采集，返回新入库的帖子数量"""
        logger.info("Starting collection round...")
        total_new = 0

        async with AsyncSessionLocal() as session:
            for collector in self._collectors:
                try:
                    posts = await collector.collect()
                    new_count = await self._save_posts(session, posts)
                    total_new += new_count
                    logger.info(
                        f"{collector} — fetched {len(posts)}, new {new_count}"
                    )
                except Exception as exc:
                    logger.error(f"{collector} — unexpected error: {exc}", exc_info=True)

        logger.info(f"Collection round complete. Total new posts: {total_new}")
        return total_new

    async def _save_posts(
        self, session: AsyncSession, posts: list[RawPostData]
    ) -> int:
        """去重并批量写入数据库，返回实际新增数量"""
        if not posts:
            return 0

        new_count = 0
        for post in posts:
            # 按 source + external_id 去重
            existing = await session.exec(
                select(RawPost).where(
                    RawPost.source == post.source,
                    RawPost.external_id == post.external_id,
                )
            )
            if existing.first():
                continue

            db_post = RawPost(
                source=post.source,
                external_id=post.external_id,
                content=post.content,
                author_name=post.author_name,
                author_id=post.author_id,
                url=post.url,
                posted_at=post.posted_at,
                collected_at=datetime.utcnow(),
                raw_metadata=json.dumps(post.metadata, ensure_ascii=False),
            )
            session.add(db_post)
            new_count += 1

        await session.commit()
        return new_count

    def start_scheduler(self) -> None:
        """启动 APScheduler 定时任务"""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler()
        interval = settings.collector_interval_minutes
        scheduler.add_job(
            self.run_once,
            "interval",
            minutes=interval,
            next_run_time=datetime.now(),   # 启动时立刻执行一次
        )
        scheduler.start()
        logger.info(f"Scheduler started — collecting every {interval} minutes")

        try:
            asyncio.get_event_loop().run_forever()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped")


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


async def _main(run_once: bool) -> None:
    await create_tables()
    manager = CollectorManager()
    if run_once:
        await manager.run_once()
    else:
        manager.start_scheduler()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anchor 采集器")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="执行一次采集后退出（不启动定时调度）",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.run_once))
