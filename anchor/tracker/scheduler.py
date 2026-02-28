"""
统一调度器
==========
APScheduler 驱动的每小时定时任务，执行 Layer3 十步流水线：

  Step 0:  AuthorProfiler       — 查询作者角色档案（LLM+联网，已查则跳过）
  Step 1:  ConditionVerifier    — 验证所有 PENDING 事实（含 evidence_tier）
  Step 2+3: LogicEvaluator     — 评估所有 Logic 的完备性并生成一句话总结
  Step 4a: ConclusionMonitor   — 为 PENDING predictive 结论配置监控信息
  Step 4b: SolutionSimulator   — 为 PENDING Solution 模拟执行 + 配置监控
  Step 5:  LogicRelationMapper — 映射逻辑间支撑关系
  Step 6:  VerdictDeriver      — 推导 Conclusion（两类型）+ Solution 裁定
  Step 7:  RoleEvaluator       — 评估作者角色与观点的匹配度
  Step 8:  PostQualityEvaluator — 评估每篇内容的独特性和有效性
  Step 9:  AuthorStatsUpdater  — 更新作者综合评估统计
"""

from __future__ import annotations

import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlmodel import select

from anchor.database.session import AsyncSessionLocal
from anchor.models import (
    Author,
    Conclusion,
    ConclusionStatus,
    ConclusionVerdict,
    Fact,
    FactStatus,
    Logic,
    RawPost,
    Solution,
    SolutionAssessment,
    SolutionStatus,
    _utcnow,
)
from anchor.tracker.author_profiler import AuthorProfiler
from anchor.tracker.author_stats_updater import AuthorStatsUpdater
from anchor.tracker.condition_verifier import ConditionVerifier
from anchor.tracker.conclusion_monitor import ConclusionMonitor
from anchor.tracker.logic_evaluator import LogicEvaluator
from anchor.tracker.post_quality_evaluator import PostQualityEvaluator
from anchor.tracker.role_evaluator import RoleEvaluator
from anchor.tracker.solution_simulator import SolutionSimulator
from anchor.tracker.verdict_deriver import VerdictDeriver


class TrackerScheduler:
    """观点追踪调度器（APScheduler）"""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._author_profiler = AuthorProfiler()
        self._verifier = ConditionVerifier()
        self._logic_evaluator = LogicEvaluator()
        self._conclusion_monitor = ConclusionMonitor()
        self._solution_simulator = SolutionSimulator()
        self._deriver = VerdictDeriver()
        self._role_evaluator = RoleEvaluator()
        self._post_quality_evaluator = PostQualityEvaluator()
        self._author_stats_updater = AuthorStatsUpdater()

    def start(self) -> None:
        """启动调度器，注册每小时任务。"""
        self._scheduler.add_job(
            self._run_layer3_pipeline,
            "interval",
            hours=1,
            id="layer3_pipeline",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("[TrackerScheduler] Scheduler started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("[TrackerScheduler] Scheduler shutdown")

    async def _run_layer3_pipeline(self) -> None:
        """每小时：执行 Layer3 六步流水线。"""
        logger.info("[TrackerScheduler] Starting Layer3 pipeline")

        async with AsyncSessionLocal() as session:
            try:
                # ── Step 0: 作者档案分析 ──────────────────────────────────────
                author_result = await session.exec(
                    select(Author).where(Author.profile_fetched == False)
                )
                unprofiled_authors = list(author_result.all())
                logger.info(
                    f"[TrackerScheduler] Step 0: profiling {len(unprofiled_authors)} authors"
                )
                for author in unprofiled_authors:
                    await self._author_profiler.profile(author, session)

                # ── Step 1: 事实验证（含 evidence_tier）───────────────────────
                result = await session.exec(
                    select(Fact).where(
                        Fact.status == FactStatus.PENDING,
                        Fact.is_verifiable == True,
                    )
                )
                pending_facts = list(result.all())
                now = _utcnow()
                active_facts = [f for f in pending_facts if _is_in_validity_window(f, now)]

                logger.info(f"[TrackerScheduler] Step 1: verifying {len(active_facts)} facts")
                for fact in active_facts:
                    await self._verifier.verify(fact, session)

                # ── Step 2+3: 逻辑评估 ──────────────────────────────────────
                logic_result = await session.exec(select(Logic))
                all_logics = list(logic_result.all())

                logger.info(f"[TrackerScheduler] Step 2+3: evaluating {len(all_logics)} logics")
                for logic in all_logics:
                    await self._logic_evaluator.evaluate(logic, session)

                # ── Step 4a: 预测型结论监控配置 ──────────────────────────────
                conc_result = await session.exec(
                    select(Conclusion).where(
                        Conclusion.conclusion_type == "predictive",
                        Conclusion.status == ConclusionStatus.PENDING,
                        Conclusion.monitoring_source_org == None,
                    )
                )
                unconfigured_concs = list(conc_result.all())

                logger.info(
                    f"[TrackerScheduler] Step 4a: configuring {len(unconfigured_concs)} "
                    f"predictive conclusions"
                )
                for conc in unconfigured_concs:
                    await self._conclusion_monitor.setup(conc, session)

                # ── Step 4b: 解决方案模拟 + 监控配置 ─────────────────────────
                sol_result = await session.exec(
                    select(Solution).where(
                        Solution.status == SolutionStatus.PENDING,
                        Solution.simulated_action_note == None,
                    )
                )
                unconfigured_sols = list(sol_result.all())

                logger.info(
                    f"[TrackerScheduler] Step 4b: simulating {len(unconfigured_sols)} solutions"
                )
                for sol in unconfigured_sols:
                    await self._solution_simulator.simulate(sol, session)

                # ── Step 5: 逻辑关系映射 ─────────────────────────────────────
                # （由 run_pipeline_test 中直接调用 LogicRelationMapper，调度器简化跳过）

                # ── Step 6: 裁定推导 ──────────────────────────────────────────
                verified_fact_ids = {f.id for f in active_facts}
                affected_conclusion_ids: set[int] = set()

                for logic in all_logics:
                    if logic.logic_type != "inference":
                        continue
                    sup = set(json.loads(logic.supporting_fact_ids or "[]"))
                    ass = set(json.loads(logic.assumption_fact_ids or "[]"))
                    if sup & verified_fact_ids or ass & verified_fact_ids:
                        if logic.conclusion_id:
                            affected_conclusion_ids.add(logic.conclusion_id)

                for cid in affected_conclusion_ids:
                    c_result = await session.exec(
                        select(Conclusion).where(Conclusion.id == cid)
                    )
                    conc = c_result.first()
                    if conc:
                        await self._deriver.derive_conclusion(conc, session)

                # 推导 Solution 裁定（仅处理监控期已到的）
                all_sol_result = await session.exec(
                    select(Solution).where(Solution.status == SolutionStatus.PENDING)
                )
                for sol in all_sol_result.all():
                    await self._deriver.derive_solution(sol, session)

                # ── Step 7: 角色匹配评估 ──────────────────────────────────────
                # 为所有新裁定（role_fit 尚未填写）执行角色匹配分析
                uneval_verdict_result = await session.exec(
                    select(ConclusionVerdict).where(ConclusionVerdict.role_fit == None)
                )
                for verdict in uneval_verdict_result.all():
                    conc_r = await session.exec(
                        select(Conclusion).where(Conclusion.id == verdict.conclusion_id)
                    )
                    conc = conc_r.first()
                    if conc is None:
                        continue
                    author_r = await session.exec(
                        select(Author).where(Author.id == conc.author_id)
                    )
                    author = author_r.first()
                    if author is None:
                        continue
                    await self._role_evaluator.evaluate_conclusion_verdict(
                        verdict, conc, author, session
                    )

                uneval_assess_result = await session.exec(
                    select(SolutionAssessment).where(SolutionAssessment.role_fit == None)
                )
                for assessment in uneval_assess_result.all():
                    sol_r = await session.exec(
                        select(Solution).where(Solution.id == assessment.solution_id)
                    )
                    sol = sol_r.first()
                    if sol is None:
                        continue
                    author_r = await session.exec(
                        select(Author).where(Author.id == sol.author_id)
                    )
                    author = author_r.first()
                    if author is None:
                        continue
                    await self._role_evaluator.evaluate_solution_assessment(
                        assessment, sol, author, session
                    )

                # ── Step 8: 内容质量评估 ──────────────────────────────────────
                # 为所有已处理但尚未质量评估的帖子执行独特性 + 有效性分析
                from anchor.models import PostQualityAssessment  # noqa: PLC0415

                processed_posts_r = await session.exec(
                    select(RawPost).where(RawPost.is_processed == True)
                )
                for raw_post in processed_posts_r.all():
                    # 查询对应的 Author（通过 MonitoredSource 或 author_platform_id）
                    if raw_post.author_platform_id:
                        a_r = await session.exec(
                            select(Author).where(
                                Author.platform_id == raw_post.author_platform_id
                            )
                        )
                        post_author = a_r.first()
                    else:
                        post_author = None
                    if post_author is None:
                        continue
                    await self._post_quality_evaluator.assess(
                        raw_post, post_author, session
                    )

                # ── Step 9: 作者综合统计更新 ──────────────────────────────────
                all_authors_r = await session.exec(select(Author))
                for a in all_authors_r.all():
                    await self._author_stats_updater.update(a, session)

                await session.commit()
                logger.info("[TrackerScheduler] Layer3 pipeline complete")

            except Exception as exc:
                logger.error(f"[TrackerScheduler] Pipeline error: {exc}")
                await session.rollback()


def _is_in_validity_window(fact: Fact, now) -> bool:
    """检查事实是否在其验证时效窗口内（null 表示不限）。"""
    if fact.validity_start and now < fact.validity_start:
        return False
    if fact.validity_end and now > fact.validity_end:
        return False
    return True
