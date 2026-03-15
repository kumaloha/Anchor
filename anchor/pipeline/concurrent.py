"""
pipeline/concurrent.py — 并发批量提取
======================================
多篇文章同时提取（LLM 调用并发），提取结果通过 WritePool FIFO 串行写入 DB。

架构：
  ┌─ URL_1 ─ collect ─ assess ─ extract_compute ─┐
  ├─ URL_2 ─ collect ─ assess ─ extract_compute ─┤  LLM 并发
  ├─ URL_3 ─ collect ─ assess ─ extract_compute ─┤
  └─ ...                                         ┘
                                                  ↓ 提取完放入队列
                                         ┌─────────────┐
                                         │  WritePool   │  FIFO 串行写入 DB
                                         │  (Queue)     │
                                         └─────────────┘

- Step 1 (采集) + Step 2 (评估): 直接并发，SQLite WAL 处理写入竞争
- Step 3a (提取计算): 纯 LLM，无 DB 操作，完全并发
- Step 3b (提取写入): 通过 WritePool FIFO 串行，保证数据一致性

Usage:
    runner = ConcurrentBatchRunner(concurrency=5)
    results = await runner.run(urls)
"""

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger


@dataclass
class PipelineResult:
    """单篇文章的处理结果。"""
    url: str
    success: bool = False
    raw_post_id: int | None = None
    domain: str | None = None
    nature: str | None = None
    content_mode: str | None = None
    assessment_summary: str | None = None
    node_count: int = 0
    edge_count: int = 0
    summary: str | None = None
    one_liner: str | None = None
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class BatchResult:
    """批量处理的汇总结果。"""
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[PipelineResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class WritePool:
    """FIFO 写入队列 — DB 写入按先进先出顺序串行执行。

    提交 async callable 到队列，后台 writer 逐个执行。
    submit() 会阻塞直到该任务执行完成，返回执行结果。
    """

    def __init__(self):
        self._queue: asyncio.Queue[tuple | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._count = 0

    async def start(self):
        self._task = asyncio.create_task(self._drain())

    async def submit(self, coro_fn) -> any:
        """提交写入任务，等待完成返回结果。"""
        future = asyncio.get_event_loop().create_future()
        await self._queue.put((coro_fn, future))
        return await future

    async def shutdown(self):
        await self._queue.put(None)
        if self._task:
            await self._task
        logger.info(f"[WritePool] 已处理 {self._count} 个写入任务")

    async def _drain(self):
        while True:
            item = await self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            coro_fn, future = item
            try:
                result = await coro_fn()
                self._count += 1
                if not future.done():
                    future.set_result(result)
            except Exception as e:
                if not future.done():
                    future.set_exception(e)
            self._queue.task_done()


class ConcurrentBatchRunner:
    """并发批量提取器。

    Args:
        concurrency: 最大并发数（同时处理的文章数）
        skip_notion: 跳过 Notion 同步
    """

    def __init__(self, concurrency: int = 5, skip_notion: bool = True):
        self.concurrency = concurrency
        self.skip_notion = skip_notion
        self._sem = asyncio.Semaphore(concurrency)
        self._pool = WritePool()

    # ── 单篇完整 pipeline ───────────────────────────────────────────────

    async def _process_one(self, url: str, index: int, total: int) -> PipelineResult:
        result = PipelineResult(url=url)
        tag = f"[{index}/{total}]"

        try:
            # ── Step 1: 采集（HTTP + 轻量 DB 写入）── 直接并发
            logger.info(f"{tag} [1/3] 采集: {url}")
            raw_post_id = await self._step_collect(url)
            if raw_post_id is None:
                result.error = "采集失败"
                return result
            result.raw_post_id = raw_post_id

            # ── 预检查 ──
            content_len = await self._check_content(raw_post_id)
            if content_len is not None and content_len < 200:
                result.skipped = True
                result.skip_reason = f"内容过短（{content_len} 字）"
                return result

            # ── Step 2: 评估（LLM + DB 写入）── 直接并发，SQLite WAL 处理
            logger.info(f"{tag} [2/3] 评估: post_id={raw_post_id}")
            assessment = await self._step_assess(raw_post_id)
            if assessment is None:
                result.error = "评估失败"
                return result

            from anchor.chains.general_assessment import resolve_content_mode
            content_mode = resolve_content_mode(
                assessment.get("content_domain"),
                assessment.get("content_nature"),
                assessment.get("content_type", ""),
            )
            result.domain = assessment.get("content_domain")
            result.nature = assessment.get("content_nature")
            result.content_mode = content_mode
            result.assessment_summary = assessment.get("assessment_summary")

            # ── 域开关检查 ──
            from anchor.config import settings
            if not settings.is_domain_enabled(content_mode):
                result.skipped = True
                result.skip_reason = f"域 '{content_mode}' 已禁用"
                result.success = True
                return result

            # ── Step 3a: 提取计算（纯 LLM，无 DB）── 并发核心！
            logger.info(f"{tag} [3/3] 提取: post_id={raw_post_id} mode={content_mode}")
            compute_result = await self._step_extract_compute(
                raw_post_id, content_mode,
            )

            # ── Step 3b: 写入 DB ── 走 WritePool FIFO 串行
            logger.info(f"{tag} 提取完成，排队写入 DB...")
            write_result = await self._pool.submit(
                lambda rpid=raw_post_id, cm=content_mode, cr=compute_result:
                    self._step_extract_write(rpid, cm, cr)
            )

            if write_result and write_result.get("is_relevant_content"):
                nodes = write_result.get("nodes", [])
                result.node_count = len(nodes) if isinstance(nodes, list) else nodes
                result.edge_count = write_result.get("edges", 0)
                result.summary = write_result.get("summary")
                result.one_liner = write_result.get("one_liner")
                logger.info(
                    f"{tag} 写入完成: {result.node_count} nodes, "
                    f"{result.edge_count} edges"
                )
            elif write_result:
                result.skipped = True
                result.skip_reason = write_result.get("skip_reason", "内容不相关")
            else:
                result.error = "提取写入返回空"
                return result

            # ── Step 4: Notion ──
            if not self.skip_notion:
                try:
                    await self._step_notion(raw_post_id)
                except Exception as e:
                    logger.warning(f"{tag} Notion 失败: {e}")

            result.success = True

        except Exception as e:
            result.error = str(e)
            logger.error(f"{tag} 失败: {url}\n{traceback.format_exc()}")

        return result

    # ── Pipeline Steps ──────────────────────────────────────────────────

    async def _step_collect(self, url: str) -> int | None:
        from anchor.database.session import AsyncSessionLocal
        from anchor.collect.input_handler import process_url

        async with AsyncSessionLocal() as s:
            result = await process_url(url, s)
        if not result or not result.raw_posts:
            return None

        rp = result.raw_posts[0]

        if not result.is_new_source:
            from anchor.commands.run_url import _refetch_and_update
            await _refetch_and_update(rp.id, url)

        async with AsyncSessionLocal() as s:
            from anchor.models import RawPost
            from sqlmodel import select
            post = (await s.exec(select(RawPost).where(RawPost.id == rp.id))).first()
            post.is_processed = False
            post.assessed = False
            post.assessed_at = None
            s.add(post)
            await s.commit()

        return rp.id

    async def _check_content(self, raw_post_id: int) -> int | None:
        """返回内容长度，None 表示文章不存在。"""
        from anchor.database.session import AsyncSessionLocal
        from anchor.models import RawPost
        from sqlmodel import select

        async with AsyncSessionLocal() as s:
            post = (await s.exec(
                select(RawPost).where(RawPost.id == raw_post_id)
            )).first()
            if not post:
                return None
            return len((post.content or "").strip())

    async def _step_assess(self, raw_post_id: int) -> dict | None:
        from anchor.database.session import AsyncSessionLocal
        from anchor.chains.general_assessment import run_assessment
        async with AsyncSessionLocal() as s:
            return await run_assessment(raw_post_id, s)

    async def _step_extract_compute(self, raw_post_id: int, content_mode: str):
        """纯 LLM 提取（无 DB 写入，可安全并发）。"""
        import datetime as _dt
        from anchor.database.session import AsyncSessionLocal
        from anchor.models import RawPost
        from sqlmodel import select

        async with AsyncSessionLocal() as s:
            rp = (await s.exec(
                select(RawPost).where(RawPost.id == raw_post_id)
            )).first()
            content = rp.enriched_content or rp.content

            if rp.media_json:
                from anchor.collect.media_describer import describe_media
                media_desc = await describe_media(rp)
                if media_desc:
                    content = content + "\n\n--- 图片内容 ---\n" + media_desc

            today = (rp.posted_at or _dt.datetime.utcnow()).date().isoformat()
            platform = rp.source
            author = rp.author_name

        if content_mode == "company":
            from anchor.extract.pipelines.company import extract_company_compute
            return await extract_company_compute(content, platform, author, today)

        from anchor.extract.pipelines.generic import extract_generic_compute
        return await extract_generic_compute(
            content, platform, author, today, domain=content_mode,
        )

    async def _step_extract_write(self, raw_post_id: int, content_mode: str, compute_result):
        """DB 写入（通过 WritePool 串行调用）。"""
        from anchor.database.session import AsyncSessionLocal
        from anchor.models import RawPost
        from sqlmodel import select

        async with AsyncSessionLocal() as s:
            rp = (await s.exec(
                select(RawPost).where(RawPost.id == raw_post_id)
            )).first()

            if content_mode == "company":
                from anchor.extract.pipelines.company import extract_company_write
                return await extract_company_write(rp, s, compute_result)

            from anchor.extract.pipelines.generic import extract_generic_write
            return await extract_generic_write(rp, s, content_mode, compute_result)

    async def _step_notion(self, raw_post_id: int):
        from anchor.database.session import AsyncSessionLocal
        from anchor.notion_sync import sync_post_to_notion
        async with AsyncSessionLocal() as s:
            await sync_post_to_notion(raw_post_id, s)
            await s.commit()

    # ── 并发入口 ────────────────────────────────────────────────────────

    async def _process_with_semaphore(
        self, url: str, index: int, total: int,
    ) -> PipelineResult:
        async with self._sem:
            return await self._process_one(url, index, total)

    async def run(self, urls: list[str]) -> BatchResult:
        """并发处理多个 URL。

        LLM 提取并发执行（受 semaphore 控制），
        DB 写入通过 WritePool FIFO 串行。
        """
        start_time = datetime.now()
        batch = BatchResult(total=len(urls))

        logger.info(
            f"[Concurrent] 开始: {len(urls)} URLs, concurrency={self.concurrency}"
        )

        await self._pool.start()

        tasks = [
            self._process_with_semaphore(url, i + 1, len(urls))
            for i, url in enumerate(urls)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        await self._pool.shutdown()

        for r in results:
            if isinstance(r, Exception):
                batch.failed += 1
                batch.results.append(PipelineResult(url="unknown", error=str(r)))
            elif r.success:
                batch.success += 1
                batch.results.append(r)
            elif r.skipped:
                batch.skipped += 1
                batch.results.append(r)
            else:
                batch.failed += 1
                batch.results.append(r)

        batch.elapsed_seconds = (datetime.now() - start_time).total_seconds()

        logger.info(
            f"[Concurrent] 完成: {batch.success}/{batch.total} 成功, "
            f"{batch.failed} 失败, {batch.skipped} 跳过, "
            f"{batch.elapsed_seconds:.1f}s"
        )

        return batch
