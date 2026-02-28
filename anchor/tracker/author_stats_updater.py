"""
Layer3 Step 9 — 作者综合评估更新器
=====================================
在所有验证/裁定步骤完成后，聚合每位作者在 7 个维度的表现数据，更新 AuthorStats 记录。

7 个维度：
  1. 事实准确率       — FactEvaluations(true) / (true+false)
  2. 结论准确性       — ConclusionVerdicts(confirmed) / 已裁定结论
  3. 预测准确性       — ConclusionVerdicts(confirmed, predictive) / 已裁定预测
  4. 逻辑严谨性       — Logic.logic_completeness 均值（complete=1.0/partial=0.6/weak=0.3/invalid=0.0）
  5. 建议可靠性       — SolutionAssessments(confirmed) / 已裁定建议
  6. 内容独特性       — PostQualityAssessment.uniqueness_score 均值
  7. 内容有效性       — PostQualityAssessment.effectiveness_score 均值

综合评分加权（0-100）：
  事实*20% + 结论*15% + 预测*20% + 逻辑*15% + 建议*15% + 独特*7.5% + 有效*7.5%
  不可用的维度（无数据）跳过，权重按比例重分配。
"""

from __future__ import annotations

import json

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.models import (
    Author,
    AuthorStats,
    Conclusion,
    ConclusionVerdict,
    EvaluationResult,
    FactEvaluation,
    Logic,
    LogicCompleteness,
    PostQualityAssessment,
    Solution,
    SolutionAssessment,
    VerdictResult,
    _utcnow,
)

_RIGOR_SCORES: dict[LogicCompleteness, float] = {
    LogicCompleteness.COMPLETE: 1.0,
    LogicCompleteness.PARTIAL: 0.6,
    LogicCompleteness.WEAK: 0.3,
    LogicCompleteness.INVALID: 0.0,
}

_WEIGHTS: dict[str, float] = {
    "fact_accuracy_rate": 0.20,
    "conclusion_accuracy_rate": 0.15,
    "prediction_accuracy_rate": 0.20,
    "logic_rigor_score": 0.15,
    "recommendation_reliability_rate": 0.15,
    "content_uniqueness_score": 0.075,
    "content_effectiveness_score": 0.075,
}

# VerdictResult 中表示"裁定中"或"已过期"的值（不纳入分母）
_UNDECIDED = {VerdictResult.PENDING, VerdictResult.EXPIRED}


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class AuthorStatsUpdater:
    """聚合作者在 7 个维度上的表现，更新 AuthorStats（Layer3 Step 9）。"""

    async def update(self, author: Author, session: AsyncSession) -> None:
        """为指定作者重新计算并 upsert AuthorStats。"""

        logger.info(
            f"[AuthorStatsUpdater] updating stats for author id={author.id} "
            f"name={author.name}"
        )

        # ── 作者的所有结论 ────────────────────────────────────────────────────
        conc_r = await session.exec(
            select(Conclusion).where(Conclusion.author_id == author.id)
        )
        conclusions = list(conc_r.all())
        conclusion_ids = [c.id for c in conclusions]
        predictive_ids = {c.id for c in conclusions if c.conclusion_type == "predictive"}

        # ── 推理逻辑（inference 类型） ────────────────────────────────────────
        if conclusion_ids:
            logic_r = await session.exec(
                select(Logic).where(
                    Logic.conclusion_id.in_(conclusion_ids),
                    Logic.logic_type == "inference",
                )
            )
            inference_logics = list(logic_r.all())
        else:
            inference_logics = []

        # ── 收集所有关联的 Fact ID ────────────────────────────────────────────
        fact_ids: set[int] = set()
        for logic in inference_logics:
            fact_ids |= set(json.loads(logic.supporting_fact_ids or "[]"))
            fact_ids |= set(json.loads(logic.assumption_fact_ids or "[]"))

        # ── FactEvaluations ───────────────────────────────────────────────────
        if fact_ids:
            fe_r = await session.exec(
                select(FactEvaluation).where(
                    FactEvaluation.fact_id.in_(list(fact_ids))
                )
            )
            fact_evals = list(fe_r.all())
        else:
            fact_evals = []

        # ── ConclusionVerdicts ────────────────────────────────────────────────
        if conclusion_ids:
            cv_r = await session.exec(
                select(ConclusionVerdict).where(
                    ConclusionVerdict.conclusion_id.in_(conclusion_ids)
                )
            )
            all_verdicts = list(cv_r.all())
        else:
            all_verdicts = []

        # ── Solutions + SolutionAssessments ──────────────────────────────────
        sol_r = await session.exec(
            select(Solution).where(Solution.author_id == author.id)
        )
        solutions = list(sol_r.all())
        solution_ids = [s.id for s in solutions]

        if solution_ids:
            sa_r = await session.exec(
                select(SolutionAssessment).where(
                    SolutionAssessment.solution_id.in_(solution_ids)
                )
            )
            sol_assessments = list(sa_r.all())
        else:
            sol_assessments = []

        # ── PostQualityAssessments ────────────────────────────────────────────
        pqa_r = await session.exec(
            select(PostQualityAssessment).where(
                PostQualityAssessment.author_id == author.id
            )
        )
        quality_assessments = list(pqa_r.all())

        # ── 计算各维度指标 ────────────────────────────────────────────────────
        # 每个 metric 格式：(value: float|None, sample: int)

        # 1. 事实准确率
        fact_decided = [
            e for e in fact_evals
            if e.result in (EvaluationResult.TRUE, EvaluationResult.FALSE)
        ]
        if fact_decided:
            true_cnt = sum(1 for e in fact_decided if e.result == EvaluationResult.TRUE)
            fact_accuracy = (true_cnt / len(fact_decided), len(fact_decided))
        else:
            fact_accuracy = (None, 0)

        # 2. 结论准确性（所有类型，已裁定）
        conc_decided = [v for v in all_verdicts if v.verdict not in _UNDECIDED]
        if conc_decided:
            conc_confirmed = sum(1 for v in conc_decided if v.verdict == VerdictResult.CONFIRMED)
            conclusion_accuracy = (conc_confirmed / len(conc_decided), len(conc_decided))
        else:
            conclusion_accuracy = (None, 0)

        # 3. 预测准确性（仅 predictive 类型，已裁定）
        pred_decided = [
            v for v in all_verdicts
            if v.conclusion_id in predictive_ids and v.verdict not in _UNDECIDED
        ]
        if pred_decided:
            pred_confirmed = sum(1 for v in pred_decided if v.verdict == VerdictResult.CONFIRMED)
            prediction_accuracy = (pred_confirmed / len(pred_decided), len(pred_decided))
        else:
            prediction_accuracy = (None, 0)

        # 4. 逻辑严谨性
        assessed_logics = [l for l in inference_logics if l.logic_completeness is not None]
        if assessed_logics:
            scores = [_RIGOR_SCORES.get(l.logic_completeness, 0.0) for l in assessed_logics]
            logic_rigor = (sum(scores) / len(scores), len(assessed_logics))
        else:
            logic_rigor = (None, 0)

        # 5. 建议可靠性（SolutionAssessments 中 confirmed 视为"可靠"）
        sol_decided = [sa for sa in sol_assessments if sa.verdict not in _UNDECIDED]
        if sol_decided:
            sol_confirmed = sum(1 for sa in sol_decided if sa.verdict == VerdictResult.CONFIRMED)
            recommendation_reliability = (sol_confirmed / len(sol_decided), len(sol_decided))
        else:
            recommendation_reliability = (None, 0)

        # 6. 内容独特性
        pqa_u = [q for q in quality_assessments if q.uniqueness_score is not None]
        if pqa_u:
            content_uniqueness = (
                sum(q.uniqueness_score for q in pqa_u) / len(pqa_u),
                len(pqa_u),
            )
        else:
            content_uniqueness = (None, 0)

        # 7. 内容有效性
        pqa_e = [q for q in quality_assessments if q.effectiveness_score is not None]
        if pqa_e:
            content_effectiveness = (
                sum(q.effectiveness_score for q in pqa_e) / len(pqa_e),
                len(pqa_e),
            )
        else:
            content_effectiveness = (None, 0)

        # ── 综合评分（加权平均，跳过无数据维度）────────────────────────────
        metric_values = {
            "fact_accuracy_rate": fact_accuracy[0],
            "conclusion_accuracy_rate": conclusion_accuracy[0],
            "prediction_accuracy_rate": prediction_accuracy[0],
            "logic_rigor_score": logic_rigor[0],
            "recommendation_reliability_rate": recommendation_reliability[0],
            "content_uniqueness_score": content_uniqueness[0],
            "content_effectiveness_score": content_effectiveness[0],
        }
        available = {k: v for k, v in metric_values.items() if v is not None}
        if available:
            total_weight = sum(_WEIGHTS[k] for k in available)
            weighted_sum = sum(_WEIGHTS[k] * v for k, v in available.items())
            overall = (weighted_sum / total_weight) * 100.0
        else:
            overall = None

        # ── Upsert AuthorStats ────────────────────────────────────────────────
        existing_r = await session.exec(
            select(AuthorStats).where(AuthorStats.author_id == author.id)
        )
        stats = existing_r.first()
        if stats is None:
            stats = AuthorStats(author_id=author.id)

        stats.fact_accuracy_rate = fact_accuracy[0]
        stats.fact_accuracy_sample = fact_accuracy[1]

        stats.conclusion_accuracy_rate = conclusion_accuracy[0]
        stats.conclusion_accuracy_sample = conclusion_accuracy[1]

        stats.prediction_accuracy_rate = prediction_accuracy[0]
        stats.prediction_accuracy_sample = prediction_accuracy[1]

        stats.logic_rigor_score = logic_rigor[0]
        stats.logic_rigor_sample = logic_rigor[1]

        stats.recommendation_reliability_rate = recommendation_reliability[0]
        stats.recommendation_reliability_sample = recommendation_reliability[1]

        stats.content_uniqueness_score = content_uniqueness[0]
        stats.content_uniqueness_sample = content_uniqueness[1]

        stats.content_effectiveness_score = content_effectiveness[0]
        stats.content_effectiveness_sample = content_effectiveness[1]

        stats.overall_credibility_score = overall
        stats.total_posts_analyzed = len(quality_assessments)
        stats.last_updated = _utcnow()

        session.add(stats)
        await session.flush()

        overall_str = f"{overall:.1f}" if overall is not None else "N/A"
        fact_str = f"{fact_accuracy[0]:.2f}" if fact_accuracy[0] is not None else "N/A"
        conc_str = f"{conclusion_accuracy[0]:.2f}" if conclusion_accuracy[0] is not None else "N/A"
        logic_str = f"{logic_rigor[0]:.2f}" if logic_rigor[0] is not None else "N/A"

        logger.info(
            f"[AuthorStatsUpdater] author id={author.id} | "
            f"overall={overall_str} | "
            f"facts={fact_str} | conc={conc_str} | logic={logic_str} | "
            f"posts={len(quality_assessments)}"
        )
