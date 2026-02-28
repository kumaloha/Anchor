"""
Layer3 Step 4 — 预测监控配置器
================================
对每个 PENDING 预测，通过 LLM 分析：

  1. 识别最合适的权威信息源（同样仅接受政府数据/上市公司财报）
  2. 计算监控时限：哪段时间的哪个数据能判断预测是否成立

结果写入 Prediction 的监控字段：
  - monitoring_source_org    — 监控机构名称
  - monitoring_source_url    — 监控数据 URL
  - monitoring_period_note   — 人读的监控时段说明
  - monitoring_start         — 监控期起点
  - monitoring_end           — 监控期终点
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Prediction, _utcnow

_MAX_TOKENS = 768

_SYSTEM_PROMPT = """\
你是一名专业的预测核查分析师。给定一条预测陈述，请：

1. 判断哪个权威信息源可用于验证这条预测是否成立
   可接受的权威来源：
   - 政府/监管机构（如国家统计局、财政部、央行、美联储、日本银行、ECB、BLS）
   - 国际金融机构（IMF、世界银行、BIS）
   - 主要交易所官方数据（NYSE、CME、上交所、港交所等）
   - 上市公司官方财报
   不接受：媒体评论、分析机构主观评级、个人判断

2. 确定监控时限：
   - 确定最早能对该预测作出有效判断的时间节点（不必等到完全兑现，关键拐点即可）
   - 若预测时限模糊或超长，选取 **3-5 年内**可观测到明显信号的监控窗口
   - 给出人读的时段描述和机器可解析的起终点日期（ISO 8601 格式）

输出必须是合法的 JSON，不加任何其他文字。
"""

_PROMPT_TEMPLATE = """\
## 待监控预测

核心陈述：{claim}
时间范围说明：{time_horizon_note}
预测发布时间：{posted_at}

## 任务

请分析这条预测，确定验证它所需的权威信息源和监控时限。

**关键要求：**
- 即使预测时限很长（如"45年回本"），也请设定 3-5 年内可观测到显著信号的监控窗口
- 优先选择能持续更新的官方数据序列（如 FRED、央行数据库）
- monitoring_start 设为预测发布日期，monitoring_end 设为合理的评估截止日

严格输出 JSON：

```json
{{
  "monitoring_source_org": "监控机构名称（如'美联储 FRED'或'U.S. Treasury'）",
  "monitoring_source_url": "监控数据 URL（可确定时填写，否则填 null）",
  "monitoring_period_note": "人读的监控时段说明（如'2021-2026年30年期美债收益率走势'）",
  "monitoring_start": "监控起点 ISO 8601 日期（yyyy-mm-dd）",
  "monitoring_end": "监控终点 ISO 8601 日期（yyyy-mm-dd，建议设为3-5年后）",
  "reason": "一句话说明为何选择该来源和时限"
}}
```

若该预测完全无法量化或无任何可观测指标，monitoring_source_org 填"无法通过权威数据验证"，
其余监控字段填 null。
"""


class PredictionMonitor:
    """为预测配置监控信息（Layer3 Step 4）。"""

    async def setup(self, prediction: Prediction, session: AsyncSession) -> None:
        """分析预测，设置监控字段（session.add 后由调用方 flush/commit）。"""

        prompt = _PROMPT_TEMPLATE.format(
            claim=prediction.claim,
            time_horizon_note=prediction.time_horizon_note or "（未指定）",
            posted_at=prediction.posted_at.strftime("%Y-%m-%d") if prediction.posted_at else "未知",
        )

        resp = await chat_completion(
            system=_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[PredictionMonitor] LLM call failed for prediction id={prediction.id}")
            return

        parsed = _parse_json(resp.content)
        if parsed is None:
            logger.warning(f"[PredictionMonitor] Parse failed for prediction id={prediction.id}")
            return

        prediction.monitoring_source_org = parsed.get("monitoring_source_org")
        prediction.monitoring_source_url = parsed.get("monitoring_source_url")
        prediction.monitoring_period_note = parsed.get("monitoring_period_note")
        prediction.monitoring_start = _parse_date(parsed.get("monitoring_start"))
        prediction.monitoring_end = _parse_date(parsed.get("monitoring_end"))
        session.add(prediction)
        await session.flush()

        logger.info(
            f"[PredictionMonitor] prediction id={prediction.id} → "
            f"org={prediction.monitoring_source_org} | "
            f"period={prediction.monitoring_period_note}"
        )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None


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
        logger.warning(f"[PredictionMonitor] JSON parse error: {exc}\nRaw: {raw[:300]}")
        return None
