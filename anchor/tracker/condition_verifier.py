"""
Layer3 Step 1 — 事实核查器（LLM 直接验证 + 权威链接）
=====================================================
直接向 LLM 寻求事实核查答案，并要求 LLM 给出支持判断的权威来源链接
（政府网页、财报数据、权威媒体）。

相比旧版两阶段流水线（Phase A 识别数据源 → Phase B 抓取数据），
本版本更简洁可靠：LLM 凭训练知识直接核实，并给出具体权威 URL 供人工核验。

结果写入：
  Fact.verified_source_org / url / data / verification_evidence
  FactEvaluation（含 result、evidence_text、evaluator_notes）
  Fact.status
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Fact,
    FactEvaluation,
    FactStatus,
    EvaluationResult,
    _utcnow,
)

_MAX_TOKENS = 3000

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SYSTEM = """\
你是一名专业事实核查员。根据提供的搜索结果（若有）和你的训练知识，判断事实陈述的真伪，\
并给出支持判断的权威来源链接。

权威来源优先级（依次降低）：
1. 政府 / 监管机构官网（如 federalreserve.gov、bls.gov、stats.gov.cn、treasury.gov、sec.gov）
2. 国际权威组织（imf.org、worldbank.org、un.org、wto.org、bis.org）
3. 上市公司官方财报页面（investor relations、SEC EDGAR、港交所披露易）
4. 权威媒体（reuters.com、bloomberg.com、ft.com、wsj.com、ap.org、bbc.com）

若搜索结果中有权威机构对该事实给出明确回应，应优先采信。

输出必须是合法 JSON，不加任何其他文字。
"""

# ---------------------------------------------------------------------------
# 用户提示：联网版（含搜索结果）
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
若完全无权威证据，填 null。
"""

_PROMPT_WITH_SEARCH = """\
今日日期：{today}

## 联网搜索结果

以下是针对该事实的实时搜索结果（来源可信度不一，请甄别）：

{search_section}

---

## 待核查事实

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实所指时间段：{validity_start} 至 {validity_end}

{semantic_expansion_guide}

{evidence_tier_guide}

## 输出要求

请综合搜索结果和你的训练知识，按照上述语义扩展规则判断事实真伪。\
若搜索结果中有权威机构（政府、央行、国际组织、上市公司财报）给出明确回应，\
应将置信度提升至 medium 或 high，result 不得使用 unavailable。

严格按照以下格式输出 JSON（所有字段必须出现）：

```json
{{
  "result": "<true|false|uncertain|unavailable>",
  "evidence_tier": <1|2|3|null>,
  "confidence": "<high|medium|low>",
  "evidence_summary": "<先说明语义扩展逻辑（若适用），再列出逐国/逐项核查结果，含具体事件和数字>",
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
- false       = 高置信度判断事实错误
- uncertain   = 事实可能正确但置信度不足（数据有争议或存在不确定性）
- unavailable = 完全无法获取任何相关信息（无搜索结果且超出训练截止日期）
"""

# ---------------------------------------------------------------------------
# 用户提示：纯训练知识版（无搜索）
# ---------------------------------------------------------------------------

_PROMPT_NO_SEARCH = """\
今日日期：{today}

请判断以下事实陈述的真伪，并给出权威来源链接。

事实陈述：{claim}
可验证表达：{verifiable_expression}
事实所指时间段：{validity_start} 至 {validity_end}

{semantic_expansion_guide}

{evidence_tier_guide}

严格按照以下格式输出 JSON（所有字段必须出现）：

```json
{{
  "result": "<true|false|uncertain|unavailable>",
  "evidence_tier": <1|2|3|null>,
  "confidence": "<high|medium|low>",
  "evidence_summary": "<先说明语义扩展逻辑（若适用），再列出逐国/逐项核查结果，含具体事件和数字>",
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
- false       = 高置信度判断事实错误
- uncertain   = 事实可能正确但置信度不足（数据有争议、时效有限制或存在不确定性）
- unavailable = 涉及未来预测、私密协议，或事实所指时间段超出知识截止日期无法核实

要求：
- authoritative_links 尽量提供 1-3 个真实权威链接；若完全无法给出，填空数组 []
- 今日日期为 {today}，事实所指时间段（{validity_start} 至 {validity_end}）若在今日之前或今日，
  则属于"当前或历史事实"，应尽力验证，不得以"未来时点"为由拒绝。
- 若事实所指时间段确实超出你的训练数据截止日期，在 evaluator_notes 中注明，result 改为 uncertain 或 unavailable。
"""


# ---------------------------------------------------------------------------
# 核查器
# ---------------------------------------------------------------------------

class ConditionVerifier:
    """直接向 LLM 寻求事实核查答案，并获取权威来源链接。"""

    async def verify(
        self, fact: Fact, session: AsyncSession
    ) -> FactEvaluation | None:
        """验证一个事实，写入 FactEvaluation，更新 Fact 状态及来源字段。"""
        if not fact.is_verifiable:
            logger.debug(f"[ConditionVerifier] fact id={fact.id} not verifiable, skip")
            return None

        parsed = await self._ask_llm(fact)
        if parsed is None:
            logger.warning(f"[ConditionVerifier] LLM call failed for fact id={fact.id}")
            return None

        # ── 解析 result ──────────────────────────────────────────────────────
        result_str = parsed.get("result", "unavailable")
        # 兼容 LLM 可能返回的同义词
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
            fact.verified_source_org  = links[0].get("org")
            fact.verified_source_url  = links[0].get("url")
        fact.verified_source_data     = json.dumps(links, ensure_ascii=False) if links else None
        fact.verification_evidence    = evidence
        fact.verified_at              = _utcnow()

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
        if llm_notes:
            notes_parts.append(llm_notes)
        evaluator_notes = " | ".join(notes_parts)

        # ── 写入 FactEvaluation ──────────────────────────────────────────────
        # 解析 evidence_tier（仅接受 1/2/3）
        tier_int: int | None = None
        if evidence_tier in (1, 2, 3):
            tier_int = evidence_tier
        elif isinstance(evidence_tier, str) and evidence_tier.strip() in ("1", "2", "3"):
            tier_int = int(evidence_tier.strip())

        fe = FactEvaluation(
            fact_id=fact.id,
            result=result_val,
            evidence_text=evidence,
            evidence_tier=tier_int,
            data_period=None,
            evaluator_notes=evaluator_notes,
        )
        session.add(fe)
        await session.flush()

        logger.info(
            f"[ConditionVerifier] fact id={fact.id} → {result_val} "
            f"confidence={confidence} links={len(links)}"
        )
        return fe

    async def _ask_llm(self, fact: Fact) -> dict | None:
        """向 LLM 发起单次核查请求（优先联网搜索）。"""
        from datetime import date
        from anchor.tracker.web_searcher import web_search, format_search_results, build_fact_query

        today_str = date.today().isoformat()
        common = dict(
            today=today_str,
            claim=fact.claim,
            verifiable_expression=fact.verifiable_expression or "（未提供）",
            validity_start=fact.validity_start_note or "不限",
            validity_end=fact.validity_end_note or "不限",
            semantic_expansion_guide=_SEMANTIC_EXPANSION_GUIDE,
            evidence_tier_guide=_EVIDENCE_TIER_GUIDE,
        )

        # ── 尝试联网搜索 ──────────────────────────────────────────────────────
        query = build_fact_query(fact.claim, fact.verifiable_expression)
        search_results = await web_search(query, max_results=5)

        if search_results:
            logger.info(
                f"[ConditionVerifier] fact id={fact.id} 搜索到 {len(search_results)} 条结果"
            )
            search_section = format_search_results(search_results)
            prompt = _PROMPT_WITH_SEARCH.format(
                search_section=search_section, **common
            )
        else:
            prompt = _PROMPT_NO_SEARCH.format(**common)

        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            return None
        return _parse_json(resp.content)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

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
