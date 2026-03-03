"""
Layer3 Step 1 — 事实核查器（两阶段：制定方案 → 执行验证）
==========================================================
重构后的两阶段流程：

  Phase A（制定检验方案）：
    调用 VerificationPlanDesigner，由多个 LLM 各自给出 3 套验证策略，
    归一化后投票选出得票最高的方案（data_source / metric / threshold）。

  Phase B（执行验证）：
    按获胜方案的 search_query 进行 Tavily 联网搜索，
    同时将提取阶段引用的权威来源也纳入参考。
    最终 LLM 按方案判断事实真伪，输出 result / evidence_tier / confidence。

可信数据源优先级：
  1. 政府 / 监管机构官网（federalreserve.gov、bls.gov、stats.gov.cn 等）
  2. 国际权威组织（imf.org、worldbank.org、un.org、bis.org 等）
  3. 上市公司官方财报（investor relations、SEC EDGAR、港交所披露易）
  4. 权威媒体引用官方数据（reuters.com、bloomberg.com、ft.com 等）

作者引用来源处理：
  若事实 references 中有作者提及的来源，在搜索结果中一并呈现，
  并由执行 LLM 依据机构权威性评估其可信度（非自动 Tier1）。

结果写入：
  Fact.verified_source_org / url / data / verification_evidence
  FactEvaluation（含 result、evidence_text、evidence_tier、verification_plan_json）
  Fact.status
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Fact,
    FactEvaluation,
    FactStatus,
    EvaluationResult,
    VerificationReference,
    _utcnow,
)

_MAX_TOKENS = 3000

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_EXEC_SYSTEM = """\
你是一名专业事实核查员。你将按照给定的验证方案，结合搜索结果和训练知识，判断事实陈述的真伪，
并给出支持判断的权威来源链接。

可信数据源优先级（依次降低）：
1. 政府 / 监管机构官网（如 federalreserve.gov、bls.gov、stats.gov.cn、treasury.gov）
2. 国际权威组织（imf.org、worldbank.org、un.org、wto.org、bis.org）
3. 上市公司官方财报页面（investor relations、SEC EDGAR、港交所披露易）
4. 权威媒体引用官方数据（reuters.com、bloomberg.com、ft.com、wsj.com）

若搜索结果中有权威机构对该事实给出明确回应，应优先采信。
若存在作者引用来源，需评估该来源机构的权威性再决定采信程度。

【重要原则】搜索结果优先于训练知识：
- 如果搜索结果和训练知识存在矛盾，优先采信搜索结果（搜索结果更新）
- 政治人物的职位、公司高管的任职等时效性信息必须以搜索结果为准，
  不得使用训练知识中可能已过时的职位信息
- 若搜索结果显示某人已晋升为更高职位，须基于新职位判断其能力/权限

输出必须是合法 JSON，不加任何其他文字。\
"""

# ---------------------------------------------------------------------------
# 语义扩展（抽象概念处理）
# ---------------------------------------------------------------------------

_SEMANTIC_EXPANSION_GUIDE = """\
## 语义扩展规则（处理抽象或聚合性陈述）

若事实陈述中包含**抽象聚合概念**（如"财富清零"、"经济崩溃"、"落败"等），\
请先将其扩展为具体可查的历史事件类型，再逐一核查，最后综合计数得出结论。

**常见扩展映射（满足任一即可认定）：**

财富清零 / 财富几乎毁灭：
  - 恶性通货膨胀（货币贬值 >90%，如魏玛德国1923年）
  - 战争掠夺 / 战败后资产被没收或强制赔款（如一战后德国、奥匈帝国、奥斯曼帝国）
  - 共产主义革命导致私有资本全面国有化（如1917年俄国、1949年中国）
  - 股市/债市永久关闭 + 货币制度崩溃（如二战中德、日、意等轴心国）
  - 主权债务违约 + 大规模资本外逃 + 殖民地丧失

1900年前后的十大强国：
  英国、美国、德国（含普鲁士）、法国、俄罗斯帝国、奥匈帝国、意大利、日本、中国（清/民国）、奥斯曼帝国

**处理步骤：**
1. 识别陈述中的抽象概念，写出你的语义扩展解释
2. 列举被提及的实体，逐一对照扩展定义核查（逐国检查）
3. 统计满足条件的数量，与陈述数字对比
4. 在 evidence_summary 中先写扩展逻辑，再列逐国结论，最后给出总计

跨年期聚合型陈述（如"过去20年"、"近十年"、"2000-2020年累计"）：
若事实涉及多年总量/累计/增长幅度，而搜索结果或训练知识只包含年度数据：
  - 将各年度数据逐年列举并累加（总量/累计场景）或计算均值（平均场景）
  - 将聚合结果与陈述中的数字直接对比，得出 true/false/uncertain
  - 在 evidence_summary 中列出各年度数据和聚合结果，再下结论
  - **不要因"无法找到多年综合定义"而返回 unavailable**
    （年度数据本身就是验证依据，汇总即可）\
"""

_EVIDENCE_TIER_GUIDE = """\
## 证据分级标准（evidence_tier）

请根据核查所用证据的来源等级填写 evidence_tier：

- **Tier 1**（最高）：权威机构直接数据
  政府统计局、央行政策声明、法院判决、监管机构报告、上市公司官方财报
  例：国家统计局数据、美联储FOMC声明、SEC公告、港交所披露

- **Tier 2**：金融市场反应数据
  股票/商品/债券/期货价格变动、指数涨跌、交易量数据
  例：黄金价格走势、标普500指数、国债收益率曲线

- **Tier 3**（最低，需能追溯至Tier1/2）：可信第三方
  引用官方数据的经济机构报告、知名财经媒体报道
  例：Bloomberg引用美联储数据的分析、IMF基于成员国统计的报告

若证据混合多个等级，填写最低等级（保守原则）。
若完全无权威证据，填 null。\
"""

# ---------------------------------------------------------------------------
# 执行阶段提示词（联网版）
# ---------------------------------------------------------------------------

_EXEC_PROMPT_WITH_SEARCH = """\
今日日期：{today}

## 验证方案（由 {vote_count} 个模型提案投票选出）

数据来源：{plan_data_source}
数据集：{plan_dataset}
核查指标：{plan_metric}
判定阈值：{plan_threshold}
搜索查询词：{plan_search_query}

## 联网搜索结果

{search_section}

{author_refs_section}

## 待核查事实

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实所指时间段：{validity_start} 至 {validity_end}

{semantic_expansion_guide}

{evidence_tier_guide}

## 执行核查

请严格按照上述验证方案，结合搜索结果和训练知识，判断事实真伪。
若搜索结果中有 Tier1/2 数据直接对应方案中的指标，优先采用并将置信度设为 high。

严格按照以下格式输出 JSON（所有字段必须出现）：

```json
{{
  "result": "<true|false|uncertain|unavailable>",
  "evidence_tier": <1|2|3|null>,
  "confidence": "<high|medium|low>",
  "evidence_summary": "<验证过程说明：使用了什么数据，对比结果如何>",
  "authoritative_links": [
    {{
      "org": "<机构名称>",
      "url": "<完整 URL>",
      "description": "<该链接包含的具体内容说明>"
    }}
  ],
  "evaluator_notes": "<额外说明，如定义边界、数据来源；若无则填 null>"
}}
```

result 说明：
- true        = 高置信度判断事实正确
- false       = 高置信度判断事实错误（有权威证据明确反驳）
- uncertain   = 事实可能正确但置信度不足（数据有争议或存在不确定性）
- unavailable = 属于以下任一情形：
    (1) 私募/机构内部数据、非公开协议、私密信息——公开来源本就不存在；
    (2) 时间段超出知识截止日期且搜索结果也无法覆盖；
    (3) 搜索结果和训练知识均无任何相关信息可供判断。
    【重要】"公开渠道找不到"≠false；若数据本身不公开，应返回 unavailable。\
"""

# ---------------------------------------------------------------------------
# 执行阶段提示词（纯训练知识版）
# ---------------------------------------------------------------------------

_EXEC_PROMPT_NO_SEARCH = """\
今日日期：{today}

## 验证方案（由 {vote_count} 个模型提案投票选出）

数据来源：{plan_data_source}
数据集：{plan_dataset}
核查指标：{plan_metric}
判定阈值：{plan_threshold}
搜索查询词：{plan_search_query}

## 待核查事实

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实所指时间段：{validity_start} 至 {validity_end}

{author_refs_section}

{semantic_expansion_guide}

{evidence_tier_guide}

请按上述验证方案，使用你的训练知识判断事实真伪，并提供权威来源链接。

严格按照以下格式输出 JSON（所有字段必须出现）：

```json
{{
  "result": "<true|false|uncertain|unavailable>",
  "evidence_tier": <1|2|3|null>,
  "confidence": "<high|medium|low>",
  "evidence_summary": "<验证过程说明：使用了什么数据，对比结果如何>",
  "authoritative_links": [
    {{
      "org": "<机构名称>",
      "url": "<完整 URL>",
      "description": "<该链接包含的具体内容说明>"
    }}
  ],
  "evaluator_notes": "<额外说明，如定义边界、时效限制；若无则填 null>"
}}
```

result 说明：
- true        = 高置信度判断事实正确
- false       = 高置信度判断事实错误（有训练知识明确反驳，且数据来自公开权威来源）
- uncertain   = 事实可能正确但置信度不足（有争议、数据缺失或范围模糊）
- unavailable = 属于以下任一情形：
    (1) 私募/机构内部数据、非公开协议、私密信息——公开来源本就不存在；
    (2) 时间段超出知识截止日期，训练知识无法覆盖；
    (3) 训练知识对该事实完全空白，无任何相关信息可供判断。
    【重要】"无法找到公开证据"≠false；若数据本身不公开，应返回 unavailable。

要求：
- authoritative_links 尽量提供 1-3 个真实权威链接；若完全无法给出，填空数组 []
- 今日日期为 {today}，事实所指时间段（{validity_start} 至 {validity_end}）若在今日之前或今日，
  则属于"当前或历史事实"，应尽力验证，不得以"未来时点"为由拒绝。\
"""


# ---------------------------------------------------------------------------
# 无搜索结果时的3视角投票常量
# ---------------------------------------------------------------------------

_NO_SEARCH_VOTE_HINTS = [
    "\n\n[分析视角 1：历史先例与实证数据]\n"
    "请重点从已有历史案例、统计数据、可量化的历史证据角度判断事实真伪。",
    "\n\n[分析视角 2：经济机制与理论]\n"
    "请重点从宏观经济学理论、内在机制与因果关系角度判断事实真伪。",
    "\n\n[分析视角 3：权威机构研究与共识]\n"
    "请重点从央行、国际组织、学术研究的实证结论与业界共识角度判断事实真伪。",
]

# ---------------------------------------------------------------------------
# Phase B0：数据源存在性检查（在3视角投票之前执行）
# ---------------------------------------------------------------------------

_SOURCE_CHECK_SYSTEM = """\
你是一名数据来源评估专家。你的任务是判断某类事实陈述是否在原则上存在可公开查阅的权威数据来源。

区分两种情况：
1. 数据源存在但当前无法获取（网络限制、数据库未索引、访问受限）→ has_public_source=true
2. 数据源本身不存在（私密内部数据、未公开协议、尚未发生的未来事件）→ has_public_source=false

输出必须是合法 JSON，不加任何其他文字。\
"""

_SOURCE_CHECK_PROMPT = """\
请判断以下事实陈述是否在原则上存在可公开查阅的权威数据来源。

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实所指时间段：{validity_start} 至 {validity_end}

【有公开数据源】的标准（满足任一即可）：
- 政府机构、央行、国际组织公开发布相关统计数据
- 上市公司官方财报、监管机构披露文件
- 历史事件有学术文献、可信媒体有留存记录
- 市场价格数据有金融数据机构收录

【无公开数据源】的典型情况（满足任一即判无）：
- 私募基金/PE/VC 的内部持仓、业绩、交易数据（私募本义就是不公开）
- 企业/政府内部决策、私密谈判、未公开协议、内部会议纪要
- 所指时间段在今日之后（尚未发生的未来事件）
- 个人私密行为、非公开内部通信、私人对话
- 事实本质上不可量化且无任何公开记录
- 境外私人机构的非强制披露数据

请严格按以下格式输出 JSON：

```json
{{
  "has_public_source": true,
  "source_type": "<来源类型简述，如'政府统计局'、'上市公司财报'、'历史学术记录'，无则null>",
  "reason": "<1-2句判断依据>"
}}
```\
"""


# ---------------------------------------------------------------------------
# 核查器
# ---------------------------------------------------------------------------


class ConditionVerifier:
    """两阶段事实核查：先制定验证方案，再执行验证。"""

    async def verify(
        self, fact: Fact, session: AsyncSession
    ) -> FactEvaluation | None:
        """验证一个事实，写入 FactEvaluation，更新 Fact 状态及来源字段。"""
        if not fact.is_verifiable:
            logger.debug(f"[ConditionVerifier] fact id={fact.id} not verifiable, skip")
            return None

        # 加载 references（提取阶段建议的权威来源）
        refs_result = await session.exec(
            select(VerificationReference).where(
                VerificationReference.fact_id == fact.id
            )
        )
        references = list(refs_result.all())

        parsed, plan = await self._run(fact, references)
        if parsed is None:
            logger.warning(f"[ConditionVerifier] 验证失败 fact id={fact.id}")
            return None

        # ── 解析 result ──────────────────────────────────────────────────────
        result_str = parsed.get("result", "unavailable")
        _ALIAS = {"unverifiable": "unavailable", "unknown": "unavailable"}
        result_str = _ALIAS.get(result_str, result_str)
        try:
            result_val = EvaluationResult(result_str)
        except ValueError:
            logger.warning(f"[ConditionVerifier] Unknown result value: {result_str!r}")
            result_val = EvaluationResult.UNAVAILABLE

        confidence    = parsed.get("confidence", "low")
        evidence      = parsed.get("evidence_summary")
        evidence_tier = parsed.get("evidence_tier")
        links         = parsed.get("authoritative_links") or []
        llm_notes     = parsed.get("evaluator_notes")

        # ── 写入 Fact 来源字段 ───────────────────────────────────────────────
        if links:
            fact.verified_source_org = links[0].get("org")
            fact.verified_source_url = links[0].get("url")
        fact.verified_source_data  = json.dumps(links, ensure_ascii=False) if links else None
        fact.verification_evidence = evidence
        fact.verified_at           = _utcnow()

        # ── 更新 Fact 状态 ───────────────────────────────────────────────────
        if result_val == EvaluationResult.TRUE:
            fact.status = FactStatus.VERIFIED_TRUE
        elif result_val == EvaluationResult.FALSE:
            fact.status = FactStatus.VERIFIED_FALSE
        elif result_val == EvaluationResult.UNAVAILABLE:
            fact.status = FactStatus.UNVERIFIABLE
        # uncertain → 保持 PENDING

        session.add(fact)

        # ── 组装 evaluator_notes ─────────────────────────────────────────────
        notes_parts = [f"[置信度={confidence}]"]
        if plan:
            notes_parts.append(f"[方案得票={plan.vote_count}] {plan.data_source}")
        if llm_notes:
            notes_parts.append(llm_notes)
        evaluator_notes = " | ".join(notes_parts)

        # ── 解析 evidence_tier ───────────────────────────────────────────────
        tier_int: int | None = None
        if evidence_tier in (1, 2, 3):
            tier_int = evidence_tier
        elif isinstance(evidence_tier, str) and evidence_tier.strip() in ("1", "2", "3"):
            tier_int = int(evidence_tier.strip())

        # ── 写入 FactEvaluation ──────────────────────────────────────────────
        fe = FactEvaluation(
            fact_id=fact.id,
            result=result_val,
            evidence_text=evidence,
            evidence_tier=tier_int,
            data_period=None,
            evaluator_notes=evaluator_notes,
            verification_plan_json=json.dumps(plan.to_dict(), ensure_ascii=False) if plan else None,
        )
        session.add(fe)
        await session.flush()

        logger.info(
            f"[ConditionVerifier] fact id={fact.id} → {result_val} "
            f"confidence={confidence} links={len(links)} "
            f"plan_votes={plan.vote_count if plan else 'N/A'}"
        )
        return fe

    async def _run(
        self,
        fact: Fact,
        references: list[VerificationReference],
    ) -> tuple[dict | None, "VerificationPlan | None"]:
        """Phase A 制定方案 + Phase B 执行验证，返回 (parsed_result, plan)。"""
        from anchor.tracker.verification_plan_designer import VerificationPlanDesigner

        # Phase A: 制定验证方案
        designer = VerificationPlanDesigner()
        plan = await designer.design(fact, references)

        if plan is None:
            # 方案设计失败，使用 fact.verifiable_expression 直接搜索
            logger.warning(
                f"[ConditionVerifier] fact id={fact.id} 方案设计失败，回退到直接验证"
            )

        # Phase B: 执行验证
        parsed = await self._execute(fact, references, plan)
        return parsed, plan

    async def _execute(
        self,
        fact: Fact,
        references: list[VerificationReference],
        plan,
    ) -> dict | None:
        """按验证方案搜索并判断事实真伪。"""
        from datetime import date
        from anchor.tracker.web_searcher import (
            web_search, format_search_results, build_fact_query
        )

        today_str = date.today().isoformat()

        # ── Phase B0（前置）：数据源存在性检查 ────────────────────────────────
        # 在联网搜索之前先判断：该事实的数据源是否原则上公开存在？
        # 若不存在（私募内部数据、未公开协议、个人私密行为等），直接返回 unavailable，
        # 无需搜索，避免 LLM 因"公开渠道找不到"而误判 false。
        logger.info(
            f"[ConditionVerifier] fact id={fact.id} Phase B0：数据源存在性检查"
        )
        has_source = await self._check_data_source_exists(fact)

        if not has_source:
            logger.info(
                f"[ConditionVerifier] fact id={fact.id} "
                f"数据源本身不公开存在，直接标记 unavailable，跳过联网搜索"
            )
            return {
                "result": "unavailable",
                "evidence_tier": None,
                "confidence": "high",
                "evidence_summary": (
                    "Phase B0 前置判定：该事实所依赖的数据在公开渠道本就不存在"
                    "（属于私募/机构内部数据、非公开协议、私密信息等）。"
                    "此类信息无法通过公开来源核实，不对真伪作出评价。"
                ),
                "authoritative_links": [],
                "evaluator_notes": (
                    "数据源不公开存在，直接标记 unavailable。"
                    "由此事实引出的结论同样不作真伪评价（verdict=unverifiable）。"
                ),
            }

        # ── 确定搜索查询词 ────────────────────────────────────────────────────
        if plan and plan.search_query:
            search_query = plan.search_query
        else:
            search_query = build_fact_query(fact.claim, fact.verifiable_expression)

        # ── 联网搜索（数据源公开存在时才搜索）───────────────────────────────
        search_results = await web_search(search_query, max_results=5)

        # 构造作者引用来源段落
        author_refs_section = _build_author_refs_section(references)

        # 方案参数（若方案设计失败则用占位符）
        plan_data_source = plan.data_source if plan else "（方案设计失败，使用通用核查）"
        plan_dataset     = plan.dataset if plan else ""
        plan_metric      = plan.metric if plan else ""
        plan_threshold   = plan.threshold if plan else ""
        plan_search_query = plan.search_query if plan else search_query
        vote_count       = plan.vote_count if plan else 0

        common = dict(
            today=today_str,
            claim=fact.claim,
            verifiable_expression=fact.verifiable_expression or "（未提供）",
            validity_start=fact.validity_start_note or "不限",
            validity_end=fact.validity_end_note or "不限",
            semantic_expansion_guide=_SEMANTIC_EXPANSION_GUIDE,
            evidence_tier_guide=_EVIDENCE_TIER_GUIDE,
            author_refs_section=author_refs_section,
            plan_data_source=plan_data_source,
            plan_dataset=plan_dataset,
            plan_metric=plan_metric,
            plan_threshold=plan_threshold,
            plan_search_query=plan_search_query,
            vote_count=vote_count,
        )

        if search_results:
            logger.info(
                f"[ConditionVerifier] fact id={fact.id} 搜索到 {len(search_results)} 条结果"
            )
            search_section = format_search_results(search_results)
            prompt = _EXEC_PROMPT_WITH_SEARCH.format(
                search_section=search_section, **common
            )
            resp = await chat_completion(
                system=_EXEC_SYSTEM,
                user=prompt,
                max_tokens=_MAX_TOKENS,
            )
            if resp is None:
                return None
            return _parse_json(resp.content)
        else:
            # 数据源公开存在但 Tavily 爬不到 → 3视角LLM投票
            logger.info(
                f"[ConditionVerifier] fact id={fact.id} 数据源存在但 Tavily 无结果，启动3视角LLM投票"
            )
            return await self._vote_no_search(fact, common)


    async def _check_data_source_exists(self, fact: Fact) -> bool:
        """Phase B0：判断该事实是否原则上存在可公开查阅的数据来源。

        返回 True  = 数据源存在（只是当前无法爬取），应走3视角LLM投票
        返回 False = 数据源本身不存在，直接标记 unavailable，不再投票
        """
        prompt = _SOURCE_CHECK_PROMPT.format(
            claim=fact.claim,
            verifiable_expression=fact.verifiable_expression or "（未提供）",
            validity_start=fact.validity_start_note or "不限",
            validity_end=fact.validity_end_note or "不限",
        )
        resp = await chat_completion(
            system=_SOURCE_CHECK_SYSTEM,
            user=prompt,
            max_tokens=300,
        )
        if resp is None:
            # 调用失败：保守处理，假设数据源存在，走投票
            logger.warning(
                f"[ConditionVerifier] fact id={fact.id} 数据源检查调用失败，保守处理走投票"
            )
            return True

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(
                f"[ConditionVerifier] fact id={fact.id} 数据源检查JSON解析失败，保守处理走投票"
            )
            return True

        has_source = parsed.get("has_public_source", True)
        source_type = parsed.get("source_type") or ""
        reason = parsed.get("reason") or ""
        logger.info(
            f"[ConditionVerifier] fact id={fact.id} 数据源检查结果: "
            f"has_source={has_source} type={source_type!r} reason={reason!r}"
        )
        return bool(has_source)

    async def _vote_no_search(self, fact: Fact, common: dict) -> dict | None:
        """无联网数据时，以3种分析视角各调用LLM一次，投票决定事实真伪。"""
        from collections import Counter

        votes: list[dict] = []
        base_prompt = _EXEC_PROMPT_NO_SEARCH.format(**common)

        for i, hint in enumerate(_NO_SEARCH_VOTE_HINTS):
            resp = await chat_completion(
                system=_EXEC_SYSTEM,
                user=base_prompt + hint,
                max_tokens=_MAX_TOKENS,
            )
            if resp:
                parsed = _parse_json(resp.content)
                if parsed:
                    votes.append(parsed)
                    logger.debug(
                        f"[ConditionVerifier] fact id={fact.id} 视角{i+1} → "
                        f"result={parsed.get('result')} confidence={parsed.get('confidence')}"
                    )

        if not votes:
            return None

        # ── 投票 ─────────────────────────────────────────────────────────────
        result_counter = Counter(v.get("result", "uncertain") for v in votes)
        _ALIAS = {"unverifiable": "unavailable", "unknown": "unavailable"}
        result_counter = Counter(
            _ALIAS.get(r, r) for r in result_counter.elements()
        )

        # 同票时优先级：true > false > uncertain > unavailable
        priority = ["true", "false", "uncertain", "unavailable"]
        max_count = max(result_counter.values())
        tied = [r for r, c in result_counter.items() if c == max_count]
        winning_result = next((p for p in priority if p in tied), tied[0])

        # 取获胜结果对应的那次调用作为基础
        winner_vote = next(
            (v for v in votes if _ALIAS.get(v.get("result"), v.get("result")) == winning_result),
            votes[0],
        )

        # 合并所有视角的 evidence_summary
        all_evidence = [
            v.get("evidence_summary", "")
            for v in votes
            if v.get("evidence_summary")
        ]
        vote_dist = dict(result_counter)
        vote_info = f"[3视角投票: {winning_result} 得{result_counter[winning_result]}票 | 分布={vote_dist}]"

        if len(all_evidence) > 1:
            combined = " | ".join(
                f"视角{i+1}: {e[:300]}" for i, e in enumerate(all_evidence)
            )
            winner_vote["evidence_summary"] = f"{vote_info} {combined}"
        elif all_evidence:
            winner_vote["evidence_summary"] = f"{vote_info} {all_evidence[0]}"

        # 使用投票中最高的置信度
        conf_rank = {"high": 3, "medium": 2, "low": 1}
        best_conf = max(
            (v.get("confidence", "low") for v in votes),
            key=lambda c: conf_rank.get(c, 0),
        )
        winner_vote["confidence"] = best_conf
        winner_vote["result"] = winning_result

        logger.info(
            f"[ConditionVerifier] fact id={fact.id} 3视角投票结果: "
            f"{winning_result} ({result_counter[winning_result]}/3票)"
        )
        return winner_vote


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _build_author_refs_section(references: list[VerificationReference]) -> str:
    """构建作者/提取阶段引用来源的段落，供执行 LLM 参考。"""
    if not references:
        return ""
    refs_text = "\n".join(
        f"  - {r.organization}：{r.data_description}"
        + (f"\n    URL: {r.url}" if r.url else "")
        for r in references
    )
    return (
        "## 提取阶段引用来源（补充参考，需评估机构权威性）\n\n"
        + refs_text
        + "\n\n（说明：政府/央行/国际组织 = 完全可信；民间机构/媒体 = 仅供参考，"
        "不得视为 Tier1/2 权威来源）\n"
    )


def _parse_json(raw: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("{")
        end   = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[ConditionVerifier] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
