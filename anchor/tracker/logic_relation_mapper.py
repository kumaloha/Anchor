"""
Layer3 Step 5 — 逻辑关系映射器
================================
分析同一篇文章中所有逻辑之间的支撑关系：
某些逻辑的结论或论证框架，可能构成其他逻辑的前提或背景依据。

例如：
  Logic A："历史上美元通过换锚延续霸权"（基于历史事实）
  Logic B："美国将成功将美元锚定算力"（基于当前技术布局）
  → A supports B：A 建立了换锚历史先例，B 的预测依赖这一先例成立

关系类型：
  supports       — from 的结论是 to 的直接逻辑前提
  contextualizes — from 为 to 提供了论证所需的背景框架，但非严格必要条件
  contradicts    — from 与 to 的前提或结论存在直接矛盾

结果写入 LogicRelation 表。
"""

from __future__ import annotations

import json
import re

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import (
    Conclusion,
    Logic,
    LogicRelation,
    Solution,
    _utcnow,
)

_MAX_TOKENS = 1200

# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------

_SYSTEM = """\
你是一名论证结构分析专家。给定同一篇文章中提取的多条逻辑论证，\
识别它们之间的「支撑关系」。

**支撑关系的判断标准：**
- supports（支撑）：Logic A 的结论是 Logic B 论证的直接前提——\
没有 A 的结论，B 的论证无法自洽或缺乏关键依据
- contextualizes（背景化）：Logic A 为 Logic B 提供了论证所需的历史框架\
或理论背景，但并非 B 的逻辑必要条件
- contradicts（矛盾）：Logic A 与 Logic B 的核心前提或结论存在直接冲突

**严格约束：**
- 不因主题相似就判断存在关系；主题相关 ≠ 逻辑依赖
- 若两条逻辑相互独立，不建立关系
- 关系必须有明确的"A 的哪个论点 → B 的哪个前提"才算成立
- 若确实无任何逻辑间依赖，输出空 relations 数组

输出必须是合法 JSON，不加任何其他文字。
"""

# ---------------------------------------------------------------------------
# 用户提示
# ---------------------------------------------------------------------------

_PROMPT = """\
以下是从同一篇文章中提取的所有逻辑论证，请识别它们之间的支撑关系。

{logics_section}

**任务：**
判断每对逻辑之间是否存在支撑关系：某条逻辑（from）的结论或论证，\
是否构成另一条逻辑（to）的前提或背景依据？

严格输出 JSON：

```json
{{
  "relations": [
    {{
      "from_logic_id": <支撑者 ID（整数）>,
      "to_logic_id": <被支撑者 ID（整数）>,
      "relation_type": "<supports|contextualizes|contradicts>",
      "note": "<一句话说明：from 的哪个论点构成了 to 的什么前提或背景>"
    }}
  ]
}}
```

若不存在任何逻辑间依赖关系，输出：{{"relations": []}}
"""


# ---------------------------------------------------------------------------
# 映射器
# ---------------------------------------------------------------------------

class LogicRelationMapper:
    """分析所有逻辑之间的支撑关系，写入 LogicRelation 表（Layer3 Step 5）。"""

    async def map(
        self, logics: list[Logic], session: AsyncSession
    ) -> list[LogicRelation]:
        """
        传入已完成评估（logic_completeness + one_sentence_summary 已填写）
        的 Logic 列表，识别关系并写入数据库。
        """
        if len(logics) < 2:
            logger.info("[LogicRelationMapper] < 2 logics，跳过关系分析")
            return []

        # ── 构建 logics 描述文本 ──────────────────────────────────────────────
        logics_section = await _build_logics_section(logics, session)

        prompt = _PROMPT.format(logics_section=logics_section)

        # ── 调用 LLM ──────────────────────────────────────────────────────────
        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning("[LogicRelationMapper] LLM call failed")
            return []

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning("[LogicRelationMapper] JSON parse failed")
            return []

        raw_relations = parsed.get("relations") or []
        valid_ids = {l.id for l in logics}
        relations: list[LogicRelation] = []

        for r in raw_relations:
            from_id = r.get("from_logic_id")
            to_id = r.get("to_logic_id")
            rtype = r.get("relation_type", "supports")
            note = r.get("note")

            # 合法性校验
            if from_id not in valid_ids or to_id not in valid_ids:
                logger.debug(f"[LogicRelationMapper] 跳过非法 ID: {from_id}→{to_id}")
                continue
            if from_id == to_id:
                continue
            if rtype not in ("supports", "contextualizes", "contradicts"):
                rtype = "supports"

            lr = LogicRelation(
                from_logic_id=from_id,
                to_logic_id=to_id,
                relation_type=rtype,
                note=note,
            )
            session.add(lr)
            relations.append(lr)
            logger.info(
                f"[LogicRelationMapper] L{from_id} --{rtype}--> L{to_id} | {note}"
            )

        if relations:
            await session.flush()

        return relations


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

async def _build_logics_section(logics: list[Logic], session: AsyncSession) -> str:
    """将所有 Logic 格式化为 LLM 可读的描述块。"""
    lines: list[str] = []
    for l in logics:
        target_label = ""
        if l.conclusion_id:
            res = await session.exec(
                select(Conclusion).where(Conclusion.id == l.conclusion_id)
            )
            conc = res.first()
            if conc:
                target_label = "结论#" + str(l.conclusion_id) + "：「" + _truncate(conc.claim, 40) + "」"
        elif l.solution_id:
            res = await session.exec(
                select(Solution).where(Solution.id == l.solution_id)
            )
            sol = res.first()
            if sol:
                target_label = "解决方案#" + str(l.solution_id) + "：「" + _truncate(sol.claim, 40) + "」"

        summary = l.one_sentence_summary or "（未评估）"
        completeness = l.logic_completeness.value if l.logic_completeness else "未知"

        block = (
            f"[Logic #{l.id}]  目标：{target_label}\n"
            f"  摘要：{summary}\n"
            f"  完备性：{completeness}"
        )
        lines.append(block)

    return "\n\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    return text[:max_len] + "…" if len(text) > max_len else text


def _parse_json(raw: str) -> dict | None:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()
    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]
    try:
        return json.loads(json_str)
    except Exception as exc:
        logger.warning(f"[LogicRelationMapper] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
