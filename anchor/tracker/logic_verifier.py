"""
Layer3 Step 1 — 逻辑验证器
============================
对 Logic.chain_summary 进行 LLM 逻辑合法性验证。

输入：logic.chain_summary（程序生成的自然语言逻辑链摘要）
LLM 判断：
  - 推理是否存在逻辑错误（形式谬误/内容谬误/循环论证等）
  - logic_validity: valid | partial | invalid
  - logic_issues: JSON 问题列表

输出写入：
  logic.logic_validity
  logic.logic_issues
  logic.logic_verified_at
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from loguru import logger
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import chat_completion
from anchor.models import Logic, _utcnow

_MAX_TOKENS = 512

_SYSTEM = """\
你是一名逻辑分析专家。给定一条推理链摘要，判断其逻辑是否合法。

输出必须是合法的 JSON，格式：
{
  "logic_validity": "valid|partial|invalid",
  "logic_issues": ["问题1", "问题2"]
}

- valid: 推理逻辑无明显问题
- partial: 存在小问题但主要逻辑成立（如前提不完整、有争议但非谬误）
- invalid: 存在明显逻辑谬误（如循环论证、稻草人谬误、以偏概全等）
- logic_issues: 具体问题列表，若无问题则为空数组 []
"""


class LogicVerifier:
    """对 Logic.chain_summary 进行逻辑合法性验证（Layer3 Step 1）。"""

    async def verify(self, logic: Logic, session: AsyncSession) -> bool:
        """验证逻辑链，写入 logic_validity/logic_issues/logic_verified_at。

        Returns:
            True 如果验证成功完成，False 如果跳过或失败
        """
        if not logic.chain_summary:
            logger.debug(f"[LogicVerifier] logic id={logic.id} has no chain_summary, skipping")
            return False

        if logic.logic_verified_at is not None:
            logger.debug(f"[LogicVerifier] logic id={logic.id} already verified, skipping")
            return False

        prompt = f"""\
请分析以下推理链是否存在逻辑错误：

{logic.chain_summary}

若存在问题，请逐条指出；若推理有效，说明推理合法。
严格输出 JSON，不加任何其他文字。
"""

        resp = await chat_completion(
            system=_SYSTEM,
            user=prompt,
            max_tokens=_MAX_TOKENS,
        )
        if resp is None:
            logger.warning(f"[LogicVerifier] LLM call failed for logic id={logic.id}")
            return False

        result = _parse_response(resp.content)
        if result is None:
            logger.warning(
                f"[LogicVerifier] Failed to parse response for logic id={logic.id}: "
                f"{resp.content[:200]}"
            )
            return False

        logic.logic_validity = result.get("logic_validity", "partial")
        issues = result.get("logic_issues", [])
        logic.logic_issues = json.dumps(issues, ensure_ascii=False)
        logic.logic_verified_at = _utcnow()

        session.add(logic)

        validity_str = logic.logic_validity
        issues_count = len(issues)
        logger.info(
            f"[LogicVerifier] logic id={logic.id} "
            f"validity={validity_str} issues={issues_count}"
        )
        return True


def _parse_response(raw: str) -> dict | None:
    """从 LLM 输出解析 JSON。"""
    # Try code block first
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        json_str = raw.strip()
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        # Normalize logic_validity
        validity = data.get("logic_validity", "partial")
        if validity not in ("valid", "partial", "invalid"):
            validity = "partial"
        data["logic_validity"] = validity
        # Ensure logic_issues is a list
        if not isinstance(data.get("logic_issues"), list):
            data["logic_issues"] = []
        return data
    except Exception as exc:
        logger.warning(f"[LogicVerifier] JSON parse error: {exc}")
        return None
