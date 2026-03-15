"""
pipelines/_base.py — 共享工具方法
===================================
LLM 调用封装、JSON 解析等纯工具函数。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from anchor.llm_client import batch_chat_completions, chat_completion
from anchor.models import Author, RawPost


async def call_llm(system: str, user: str, max_tokens: int) -> str | None:
    resp = await chat_completion(system=system, user=user, max_tokens=max_tokens)
    if resp is None:
        return None
    logger.debug(f"LLM: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}")
    return resp.content


async def call_llm_batch(
    requests: list[tuple[str, str, int]],
) -> list[str | None]:
    """批量 LLM 调用（自动走 Batch API 或串行退化）。

    Args:
        requests: [(system, user, max_tokens), ...]

    Returns:
        与 requests 等长的 content 字符串列表（失败为 None）。
    """
    responses = await batch_chat_completions(requests)
    results = []
    for resp in responses:
        if resp is None:
            results.append(None)
        else:
            logger.debug(f"LLM batch: model={resp.model} in={resp.input_tokens} out={resp.output_tokens}")
            results.append(resp.content)
    return results


def parse_json(raw: str, model_cls, step_name: str):
    """从 LLM 返回文本中提取 JSON 并解析为给定 Pydantic 模型。"""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    json_str = match.group(1) if match else raw.strip()

    if not match:
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start == -1 or end == 0:
            logger.warning(f"{step_name}: no JSON found in output")
            return None
        json_str = json_str[start:end]

    try:
        data = json.loads(json_str)
        return model_cls.model_validate(data)
    except Exception as exc:
        logger.warning(f"{step_name} parse error: {exc}\nRaw: {raw[:400]}")
        return None


# ── 通用安全转换工具 ─────────────────────────────────────────────────────


def safe_float(val) -> float | None:
    """清洗并转换为 float，处理 '0.309%' 等。"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().rstrip("%")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def safe_str(val) -> str | None:
    """任意值转字符串，list 自动拼接。"""
    if val is None:
        return None
    if isinstance(val, list):
        return ", ".join(str(v) for v in val) if val else None
    return str(val)


async def get_or_create_author(session: AsyncSession, raw_post: RawPost) -> Author:
    if raw_post.author_platform_id:
        result = await session.exec(
            select(Author).where(
                Author.platform == raw_post.source,
                Author.platform_id == raw_post.author_platform_id,
            )
        )
        author = result.first()
        if author:
            return author

    author = Author(
        name=raw_post.author_name,
        platform=raw_post.source,
        platform_id=raw_post.author_platform_id,
        profile_url=f"https://{raw_post.source}.com/{raw_post.author_platform_id}",
    )
    session.add(author)
    await session.flush()
    return author
