"""
裁定推导器
==========
根据 Logic 中的事实验证结果推导 Conclusion 的最终裁定，
以及根据监控期数据推导 Solution 的评估结果。

Conclusion 推导逻辑：
  retrospective 结论 — 立即可用 FactEvaluation 推导裁定
  predictive 结论    — monitoring_end 之后才推导（未到期则返回 PENDING）

  推导规则（inference Logic）：
    1. 加载目标的 Logic 记录（取最新一条）
    2. 加载 supporting_facts 和 assumption_facts 的最新 FactEvaluation
    3. 按以下规则映射到 VerdictResult：
       - 所有事实均 unavailable           → unverifiable
       - 任意 assumption 为 false         → refuted（前提条件不成立）
       - 任意 supporting 为 false         → refuted（证据被反驳）
       - 全部 supporting 为 true 且 assumption 全部 true/unavailable → confirmed
       - 存在 true 但也存在 uncertain/unavailable → partial
       - 其他                             → pending
    4. 写 ConclusionVerdict + logic_trace JSON
    5. 回写 Conclusion.status

Solution 推导逻辑（derive_solution）：
  - 对 PENDING Solution 在 monitoring_end 后触发评估
  - 写入 SolutionAssessment + 更新 Solution.status
"""

from __future__ import annotations

import json
from datetime import datetime

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    Conclusion,
    ConclusionStatus,
    ConclusionVerdict,
    EvaluationResult,
    Fact,
    FactEvaluation,
    Logic,
    Solution,
    SolutionAssessment,
    SolutionStatus,
    VerdictResult,
    _utcnow,
)


class VerdictDeriver:
    """根据事实评估结果和逻辑关系推导观点裁定。"""

    async def derive_conclusion(
        self, conclusion: Conclusion, session: AsyncSession
    ) -> ConclusionVerdict | None:
        """推导结论裁定，写入 ConclusionVerdict，更新 Conclusion.status。

        retrospective 结论：立即推导
        predictive 结论：monitoring_end 后才推导，否则返回 None（保持 PENDING）
        """
        # 预测型结论：检查监控期是否已到
        if conclusion.conclusion_type == "predictive":
            now = _utcnow()
            if conclusion.monitoring_end and now < conclusion.monitoring_end:
                logger.debug(
                    f"[VerdictDeriver] conclusion id={conclusion.id} predictive, "
                    f"monitoring_end={conclusion.monitoring_end} not reached, skip"
                )
                return None

        logic = await _load_inference_logic(session, conclusion_id=conclusion.id)
        if logic is None:
            logger.debug(f"[VerdictDeriver] conclusion id={conclusion.id} has no inference logic")
            return None

        supporting_ids = json.loads(logic.supporting_fact_ids or "[]")
        assumption_ids = json.loads(logic.assumption_fact_ids or "[]")
        all_fact_ids = list(set(supporting_ids + assumption_ids))

        evaluations = await _load_latest_fact_evaluations(session, all_fact_ids)
        verdict, logic_trace = _derive(supporting_ids, assumption_ids, evaluations)

        cv = ConclusionVerdict(
            conclusion_id=conclusion.id,
            verdict=verdict,
            logic_trace=json.dumps(logic_trace, ensure_ascii=False),
            derived_at=_utcnow(),
        )
        session.add(cv)

        conclusion.status = _verdict_to_conclusion_status(verdict)
        session.add(conclusion)

        await session.flush()
        logger.info(f"[VerdictDeriver] conclusion id={conclusion.id} verdict: {verdict}")
        return cv

    async def derive_solution(
        self, solution: Solution, session: AsyncSession
    ) -> SolutionAssessment | None:
        """推导解决方案评估，写入 SolutionAssessment，更新 Solution.status。

        仅在 monitoring_end 之后触发，否则返回 None（保持 PENDING）。
        """
        if solution.status != SolutionStatus.PENDING:
            return None

        now = _utcnow()
        if solution.monitoring_end and now < solution.monitoring_end:
            logger.debug(
                f"[VerdictDeriver] solution id={solution.id} "
                f"monitoring_end={solution.monitoring_end} not reached, skip"
            )
            return None

        # 解决方案裁定：基于源结论的裁定状态做简单映射
        logic_result = await session.exec(
            select(Logic).where(
                Logic.logic_type == "derivation",
                Logic.solution_id == solution.id,
            )
        )
        derivation_logic = logic_result.first()

        source_conc_ids: list[int] = []
        if derivation_logic and derivation_logic.source_conclusion_ids:
            try:
                source_conc_ids = json.loads(derivation_logic.source_conclusion_ids)
            except Exception:
                pass

        # 查询源结论的最新裁定
        verdict = VerdictResult.PENDING
        if source_conc_ids:
            cv_result = await session.exec(
                select(ConclusionVerdict)
                .where(ConclusionVerdict.conclusion_id.in_(source_conc_ids))
                .order_by(ConclusionVerdict.derived_at.desc())
            )
            all_verdicts = [v.verdict for v in cv_result.all()]
            if all_verdicts:
                verdict = _aggregate_solution_verdict(all_verdicts)

        assessment = SolutionAssessment(
            solution_id=solution.id,
            verdict=verdict,
            evidence_text=f"基于 {len(source_conc_ids)} 条源结论裁定推导",
            assessed_at=_utcnow(),
        )
        session.add(assessment)

        # 更新 Solution 状态
        solution.status = _verdict_to_solution_status(verdict)
        session.add(solution)

        await session.flush()
        logger.info(f"[VerdictDeriver] solution id={solution.id} verdict: {verdict}")
        return assessment


# ---------------------------------------------------------------------------
# 内部逻辑
# ---------------------------------------------------------------------------

async def _load_inference_logic(
    session: AsyncSession,
    conclusion_id: int,
) -> Logic | None:
    """加载结论的最新 inference Logic 记录。"""
    result = await session.exec(
        select(Logic).where(
            Logic.logic_type == "inference",
            Logic.conclusion_id == conclusion_id,
        ).order_by(Logic.id.desc())
    )
    return result.first()


async def _load_latest_fact_evaluations(
    session: AsyncSession, fact_ids: list[int]
) -> dict[int, EvaluationResult]:
    """返回 {fact_id: 最新 EvaluationResult}"""
    if not fact_ids:
        return {}
    result = await session.exec(
        select(FactEvaluation)
        .where(FactEvaluation.fact_id.in_(fact_ids))
        .order_by(FactEvaluation.evaluated_at.desc())
    )
    all_evals = list(result.all())
    latest: dict[int, EvaluationResult] = {}
    for ev in all_evals:
        if ev.fact_id not in latest:
            latest[ev.fact_id] = ev.result
    return latest


def _derive(
    supporting_ids: list[int],
    assumption_ids: list[int],
    evaluations: dict[int, EvaluationResult],
) -> tuple[VerdictResult, dict]:
    """推导裁定逻辑。"""

    def get_result(fact_id: int) -> EvaluationResult:
        return evaluations.get(fact_id, EvaluationResult.UNAVAILABLE)

    supporting_results = {fid: get_result(fid) for fid in supporting_ids}
    assumption_results = {fid: get_result(fid) for fid in assumption_ids}
    all_results = list(supporting_results.values()) + list(assumption_results.values())

    # 全部 unavailable → unverifiable
    if all_results and all(r == EvaluationResult.UNAVAILABLE for r in all_results):
        verdict = VerdictResult.UNVERIFIABLE

    # 任意假设条件为 false → refuted（前提不成立）
    elif any(r == EvaluationResult.FALSE for r in assumption_results.values()):
        verdict = VerdictResult.REFUTED

    # 任意支撑事实为 false → refuted（证据被反驳）
    elif any(r == EvaluationResult.FALSE for r in supporting_results.values()):
        verdict = VerdictResult.REFUTED

    # 全部 true → confirmed
    elif all_results and all(
        r in (EvaluationResult.TRUE, EvaluationResult.UNAVAILABLE)
        for r in all_results
    ) and any(r == EvaluationResult.TRUE for r in all_results):
        verdict = VerdictResult.CONFIRMED

    # 有 true 也有 uncertain/unavailable → partial
    elif any(r == EvaluationResult.TRUE for r in all_results):
        verdict = VerdictResult.PARTIAL

    else:
        verdict = VerdictResult.PENDING

    logic_trace = {
        "supporting_facts": {str(fid): r.value for fid, r in supporting_results.items()},
        "assumption_facts": {str(fid): r.value for fid, r in assumption_results.items()},
        "verdict": verdict.value,
    }
    return verdict, logic_trace


def _aggregate_solution_verdict(verdicts: list[VerdictResult]) -> VerdictResult:
    """从多个源结论裁定聚合解决方案裁定。"""
    if all(v == VerdictResult.CONFIRMED for v in verdicts):
        return VerdictResult.CONFIRMED
    if any(v == VerdictResult.REFUTED for v in verdicts):
        return VerdictResult.REFUTED
    if any(v == VerdictResult.CONFIRMED for v in verdicts):
        return VerdictResult.PARTIAL
    if all(v == VerdictResult.UNVERIFIABLE for v in verdicts):
        return VerdictResult.UNVERIFIABLE
    return VerdictResult.PENDING


def _verdict_to_conclusion_status(verdict: VerdictResult) -> ConclusionStatus:
    mapping = {
        VerdictResult.CONFIRMED: ConclusionStatus.CONFIRMED,
        VerdictResult.REFUTED: ConclusionStatus.REFUTED,
        VerdictResult.UNVERIFIABLE: ConclusionStatus.UNVERIFIABLE,
    }
    return mapping.get(verdict, ConclusionStatus.PENDING)


def _verdict_to_solution_status(verdict: VerdictResult) -> SolutionStatus:
    mapping = {
        VerdictResult.CONFIRMED: SolutionStatus.VALIDATED,
        VerdictResult.PARTIAL: SolutionStatus.VALIDATED,
        VerdictResult.REFUTED: SolutionStatus.INVALIDATED,
        VerdictResult.UNVERIFIABLE: SolutionStatus.UNVERIFIABLE,
    }
    return mapping.get(verdict, SolutionStatus.PENDING)
