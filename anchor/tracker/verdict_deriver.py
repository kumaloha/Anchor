"""
裁定推导器（v2 — 六实体）
============================
根据 alignment_result（现实对齐结果）和 logic_validity 推导 Conclusion/Prediction 最终裁定，
以及根据监控期数据推导 Solution 的评估结果。

Conclusion 推导逻辑（读取 inline alignment_result）：
  - 立即推导（retrospective 类型）
  - 支撑事实全部 alignment_result=true 且 logic_validity=valid/partial → confirmed
  - 任意 alignment_result=false → refuted
  - logic_validity=invalid → refuted
  - 混合 true/uncertain → partial
  - 全部 unavailable → unverifiable

Prediction 推导逻辑：
  - monitoring_end 后才推导，否则返回 None（保持 PENDING）
  - 读取 prediction.alignment_result（由 RealityAligner 填写）
  - 写入 PredictionVerdict

Solution 推导逻辑：
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
    Fact,
    FactEvaluation,
    EvaluationResult,
    Logic,
    Prediction,
    PredictionStatus,
    PredictionVerdict,
    Solution,
    SolutionAssessment,
    SolutionStatus,
    VerdictResult,
    _utcnow,
)

# Alignment result values
_ALIGN_TRUE = "true"
_ALIGN_FALSE = "false"
_ALIGN_UNCERTAIN = "uncertain"
_ALIGN_UNAVAILABLE = "unavailable"


class VerdictDeriver:
    """根据现实对齐结果和逻辑验证推导观点裁定。"""

    async def derive_conclusion(
        self, conclusion: Conclusion, session: AsyncSession
    ) -> ConclusionVerdict | None:
        """推导结论裁定（retrospective 类型立即推导）。

        读取该结论的 inference Logic，以及相关 Fact 的 alignment_result，
        结合 logic_validity，推导裁定。
        """
        logic = await _load_inference_logic(session, conclusion_id=conclusion.id)

        # Try alignment-based verdict if facts have alignment_result
        verdict, logic_trace = await _derive_from_alignment(
            session, conclusion=conclusion, logic=logic
        )

        # Fall back to old FactEvaluation-based verdict if no alignment data
        if verdict == VerdictResult.PENDING and logic is not None:
            supporting_ids = json.loads(logic.supporting_fact_ids or "[]")
            assumption_ids = json.loads(logic.assumption_fact_ids or "[]")
            evaluations = await _load_latest_fact_evaluations(
                session, list(set(supporting_ids + assumption_ids))
            )
            if evaluations:
                verdict, logic_trace = _derive_from_evaluations(
                    supporting_ids, assumption_ids, evaluations
                )

        # Include author confidence in logic_trace
        if conclusion.author_confidence:
            logic_trace["author_confidence"] = conclusion.author_confidence
        if conclusion.author_confidence_note:
            logic_trace["author_confidence_note"] = conclusion.author_confidence_note

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

    async def derive_prediction(
        self, prediction: Prediction, session: AsyncSession
    ) -> PredictionVerdict | None:
        """推导预测裁定，写入 PredictionVerdict。

        monitoring_end 后才推导，否则返回 None（保持 PENDING）。
        优先读取 prediction.alignment_result（由 RealityAligner 在监控期后填写）。
        """
        now = _utcnow()
        if prediction.monitoring_end and now < prediction.monitoring_end:
            logger.debug(
                f"[VerdictDeriver] prediction id={prediction.id} "
                f"monitoring_end={prediction.monitoring_end} not reached, skip"
            )
            return None

        # Derive verdict from alignment_result
        alignment = prediction.alignment_result
        if alignment == _ALIGN_TRUE:
            verdict = VerdictResult.CONFIRMED
        elif alignment == _ALIGN_FALSE:
            verdict = VerdictResult.REFUTED
        elif alignment == _ALIGN_UNCERTAIN:
            verdict = VerdictResult.PARTIAL
        elif alignment == _ALIGN_UNAVAILABLE:
            verdict = VerdictResult.UNVERIFIABLE
        else:
            verdict = VerdictResult.PENDING

        logic_trace: dict = {
            "alignment_result": alignment,
            "alignment_evidence": prediction.alignment_evidence,
            "alignment_tier": prediction.alignment_tier,
        }
        if prediction.author_confidence:
            logic_trace["author_confidence"] = prediction.author_confidence
        if prediction.author_confidence_note:
            logic_trace["author_confidence_note"] = prediction.author_confidence_note

        pv = PredictionVerdict(
            prediction_id=prediction.id,
            verdict=verdict,
            logic_trace=json.dumps(logic_trace, ensure_ascii=False),
            derived_at=_utcnow(),
        )
        session.add(pv)

        prediction.status = _verdict_to_prediction_status(verdict)
        session.add(prediction)

        await session.flush()
        logger.info(f"[VerdictDeriver] prediction id={prediction.id} verdict: {verdict}")
        return pv

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

        logic_result = await session.exec(
            select(Logic).where(
                Logic.logic_type == "derivation",
                Logic.solution_id == solution.id,
            )
        )
        derivation_logic = logic_result.first()

        source_conc_ids: list[int] = []
        source_pred_ids: list[int] = []
        if derivation_logic:
            if derivation_logic.source_conclusion_ids:
                try:
                    source_conc_ids = json.loads(derivation_logic.source_conclusion_ids)
                except Exception:
                    pass
            if derivation_logic.source_prediction_ids:
                try:
                    source_pred_ids = json.loads(derivation_logic.source_prediction_ids)
                except Exception:
                    pass

        # Aggregate from source conclusions and predictions
        all_verdicts: list[VerdictResult] = []

        if source_conc_ids:
            cv_result = await session.exec(
                select(ConclusionVerdict)
                .where(ConclusionVerdict.conclusion_id.in_(source_conc_ids))
                .order_by(ConclusionVerdict.derived_at.desc())
            )
            all_verdicts.extend(v.verdict for v in cv_result.all())

        if source_pred_ids:
            pv_result = await session.exec(
                select(PredictionVerdict)
                .where(PredictionVerdict.prediction_id.in_(source_pred_ids))
                .order_by(PredictionVerdict.derived_at.desc())
            )
            all_verdicts.extend(v.verdict for v in pv_result.all())

        verdict = VerdictResult.PENDING
        if all_verdicts:
            verdict = _aggregate_solution_verdict(all_verdicts)

        total_sources = len(source_conc_ids) + len(source_pred_ids)
        assessment = SolutionAssessment(
            solution_id=solution.id,
            verdict=verdict,
            evidence_text=f"基于 {total_sources} 条源结论/预测裁定推导",
            assessed_at=_utcnow(),
        )
        session.add(assessment)

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
    result = await session.exec(
        select(Logic).where(
            Logic.logic_type == "inference",
            Logic.conclusion_id == conclusion_id,
        ).order_by(Logic.id.desc())
    )
    return result.first()


async def _derive_from_alignment(
    session: AsyncSession,
    conclusion: Conclusion,
    logic: Logic | None,
) -> tuple[VerdictResult, dict]:
    """从 alignment_result（inline 字段）推导结论裁定。"""
    logic_trace: dict = {}

    # Get supporting fact IDs and assumption fact IDs from Logic
    supporting_ids: list[int] = []
    assumption_ids: list[int] = []
    if logic:
        try:
            supporting_ids = json.loads(logic.supporting_fact_ids or "[]")
        except Exception:
            pass
        try:
            assumption_ids = json.loads(logic.assumption_fact_ids or "[]")
        except Exception:
            pass

    all_fact_ids = list(set(supporting_ids + assumption_ids))

    # Load facts with alignment_result
    supporting_alignments: dict[int, str] = {}
    assumption_alignments: dict[int, str] = {}

    if all_fact_ids:
        facts_result = await session.exec(
            select(Fact).where(Fact.id.in_(all_fact_ids))
        )
        for fact in facts_result.all():
            ar = fact.alignment_result or _ALIGN_UNAVAILABLE
            if fact.id in supporting_ids:
                supporting_alignments[fact.id] = ar
            if fact.id in assumption_ids:
                assumption_alignments[fact.id] = ar

    # Also include conclusion's own alignment_result
    conc_alignment = conclusion.alignment_result

    logic_trace["supporting_alignments"] = {
        str(fid): r for fid, r in supporting_alignments.items()
    }
    logic_trace["assumption_alignments"] = {
        str(fid): r for fid, r in assumption_alignments.items()
    }
    logic_trace["conclusion_alignment"] = conc_alignment

    # Check logic_validity
    logic_validity = None
    if logic:
        logic_validity = logic.logic_validity
    logic_trace["logic_validity"] = logic_validity

    # Invalid logic → refuted
    if logic_validity == "invalid":
        logic_trace["verdict"] = VerdictResult.REFUTED.value
        return VerdictResult.REFUTED, logic_trace

    all_alignments = (
        list(supporting_alignments.values())
        + list(assumption_alignments.values())
    )
    if conc_alignment:
        all_alignments.append(conc_alignment)

    if not all_alignments:
        logic_trace["verdict"] = VerdictResult.PENDING.value
        return VerdictResult.PENDING, logic_trace

    # Assumption false → refuted
    if any(r == _ALIGN_FALSE for r in assumption_alignments.values()):
        verdict = VerdictResult.REFUTED

    # Any supporting false → refuted
    elif any(r == _ALIGN_FALSE for r in supporting_alignments.values()):
        verdict = VerdictResult.REFUTED

    # Conclusion itself false → refuted
    elif conc_alignment == _ALIGN_FALSE:
        verdict = VerdictResult.REFUTED

    # All unavailable → unverifiable
    elif all(r == _ALIGN_UNAVAILABLE for r in all_alignments):
        verdict = VerdictResult.UNVERIFIABLE

    # All true (with possible unavailable) → confirmed
    elif all(r in (_ALIGN_TRUE, _ALIGN_UNAVAILABLE) for r in all_alignments) and any(
        r == _ALIGN_TRUE for r in all_alignments
    ):
        verdict = VerdictResult.CONFIRMED

    # Mixed true + uncertain/unavailable → partial
    elif any(r == _ALIGN_TRUE for r in all_alignments):
        verdict = VerdictResult.PARTIAL

    else:
        verdict = VerdictResult.PENDING

    logic_trace["verdict"] = verdict.value
    return verdict, logic_trace


async def _load_latest_fact_evaluations(
    session: AsyncSession, fact_ids: list[int]
) -> dict[int, EvaluationResult]:
    """返回 {fact_id: 最新 EvaluationResult}（兼容旧数据）"""
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


def _derive_from_evaluations(
    supporting_ids: list[int],
    assumption_ids: list[int],
    evaluations: dict[int, EvaluationResult],
) -> tuple[VerdictResult, dict]:
    """兼容旧 FactEvaluation 推导裁定逻辑。"""

    def get_result(fact_id: int) -> EvaluationResult:
        return evaluations.get(fact_id, EvaluationResult.UNAVAILABLE)

    supporting_results = {fid: get_result(fid) for fid in supporting_ids}
    assumption_results = {fid: get_result(fid) for fid in assumption_ids}
    all_results = list(supporting_results.values()) + list(assumption_results.values())

    if all_results and all(r == EvaluationResult.UNAVAILABLE for r in all_results):
        verdict = VerdictResult.UNVERIFIABLE
    elif any(r == EvaluationResult.FALSE for r in assumption_results.values()):
        verdict = VerdictResult.REFUTED
    elif any(r == EvaluationResult.FALSE for r in supporting_results.values()):
        verdict = VerdictResult.REFUTED
    elif all_results and all(
        r in (EvaluationResult.TRUE, EvaluationResult.UNAVAILABLE) for r in all_results
    ) and any(r == EvaluationResult.TRUE for r in all_results):
        verdict = VerdictResult.CONFIRMED
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


def _verdict_to_prediction_status(verdict: VerdictResult) -> PredictionStatus:
    mapping = {
        VerdictResult.CONFIRMED: PredictionStatus.CONFIRMED,
        VerdictResult.REFUTED: PredictionStatus.REFUTED,
        VerdictResult.UNVERIFIABLE: PredictionStatus.UNVERIFIABLE,
        VerdictResult.PARTIAL: PredictionStatus.AWAITING,
    }
    return mapping.get(verdict, PredictionStatus.PENDING)


def _verdict_to_solution_status(verdict: VerdictResult) -> SolutionStatus:
    mapping = {
        VerdictResult.CONFIRMED: SolutionStatus.VALIDATED,
        VerdictResult.PARTIAL: SolutionStatus.VALIDATED,
        VerdictResult.REFUTED: SolutionStatus.INVALIDATED,
        VerdictResult.UNVERIFIABLE: SolutionStatus.UNVERIFIABLE,
    }
    return mapping.get(verdict, SolutionStatus.PENDING)
