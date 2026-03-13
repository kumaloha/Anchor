"""
统一 LLM 客户端
===============
屏蔽 Anthropic SDK 和 OpenAI SDK 的差异，提供统一的 chat_completion 接口。

Batch 模式（enable_batch=True + llm_provider=openai）：
  通过 OpenAI Batch API 提交请求（DashScope/Qwen 兼容），享受 50% 成本折扣。
  异步提交 JSONL → 轮询 → 下载结果。

配置方式（.env）：
  # 使用 Anthropic（默认）
  LLM_PROVIDER=anthropic
  ANTHROPIC_API_KEY=sk-ant-...

  # 使用 OpenAI 兼容接口（Qwen / DeepSeek 等）
  LLM_PROVIDER=openai
  LLM_API_KEY=sk-...
  LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  LLM_MODEL=qwen-plus

  # Batch 模式（50% 折扣）
  ENABLE_BATCH=true
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from anchor.config import settings


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int


async def chat_completion(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> Optional[LLMResponse]:
    """调用 LLM，返回文本响应。失败返回 None。

    Args:
        model: 覆盖默认模型（用于多模型方案设计场景）。None 则使用 settings 配置的主模型。
    """
    if _is_openai_mode():
        return await _openai_completion(system, user, max_tokens, model=model)
    return await _anthropic_completion(system, user, max_tokens, model=model)


async def batch_chat_completions(
    requests: list[tuple[str, str, int]],
    model: str | None = None,
) -> list[LLMResponse | None]:
    """批量 LLM 调用。

    当 enable_batch=True 且 llm_provider=openai 时，走 Batch API（50% 折扣）。
    否则退化为串行实时调用。

    Args:
        requests: [(system, user, max_tokens), ...]
        model:    覆盖默认模型

    Returns:
        与 requests 等长的列表，每个元素为 LLMResponse 或 None（失败）。
    """
    if not requests:
        return []

    # 单条请求直接走实时
    if len(requests) == 1:
        r = await chat_completion(*requests[0], model=model)
        return [r]

    # Batch API 仅 OpenAI 模式 + enable_batch
    if _is_openai_mode() and settings.enable_batch:
        return await _openai_batch(requests, model=model)

    # 退化：串行实时调用
    results = []
    for sys, usr, max_tok in requests:
        r = await chat_completion(sys, usr, max_tok, model=model)
        results.append(r)
    return results


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
) -> str | None:
    """将音频文件转录为文字（Whisper 兼容 API）。"""
    from loguru import logger

    api_key  = settings.asr_api_key or settings.llm_api_key
    base_url = settings.asr_base_url or None
    model    = settings.asr_model or "whisper-1"

    if not api_key:
        logger.warning("[ASR] asr_api_key 和 llm_api_key 均未配置，跳过转录")
        return None

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        with open(audio_path, "rb") as f:
            kwargs: dict = {"model": model, "file": f}
            if language:
                kwargs["language"] = language
            result = await client.audio.transcriptions.create(**kwargs)
        text = result.text.strip()
        logger.debug(f"[ASR] 转录完成，{len(text)} 字符")
        return text
    except Exception as exc:
        logger.error(f"[ASR] 转录失败: {exc}")
        return None


async def chat_completion_multimodal(
    system: str,
    user: str,
    image_url: str,
    max_tokens: int = 1024,
) -> Optional[LLMResponse]:
    """调用视觉 LLM，传入图片 URL + 文本，返回图片描述。失败返回 None。"""
    if _is_openai_mode():
        return await _openai_vision_completion(system, user, image_url, max_tokens)
    return await _anthropic_vision_completion(system, user, image_url, max_tokens)


# ---------------------------------------------------------------------------
# 内部：判断使用哪个后端
# ---------------------------------------------------------------------------


def _is_openai_mode() -> bool:
    return settings.llm_provider.lower() == "openai" and bool(settings.llm_api_key)


def _get_openai_model() -> str:
    return settings.llm_model or "gpt-4o-mini"


def _get_openai_vision_model() -> str:
    return settings.llm_vision_model or settings.llm_model or "gpt-4o-mini"


def _get_anthropic_model() -> str:
    return settings.llm_model or "claude-sonnet-4-6"


def _get_anthropic_vision_model() -> str:
    return settings.llm_vision_model or settings.llm_model or "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# OpenAI Batch API（Qwen DashScope 兼容）
# ---------------------------------------------------------------------------


async def _openai_batch(
    requests: list[tuple[str, str, int]],
    model: str | None = None,
) -> list[LLMResponse | None]:
    """通过 OpenAI Batch API 提交批量请求，轮询等待结果。

    流程：写 JSONL → 上传文件 → 创建 Batch → 轮询 → 下载结果 → 解析
    """
    from openai import AsyncOpenAI
    from loguru import logger

    use_model = model or _get_openai_model()
    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )

    # ── 1. 写 JSONL 临时文件 ─────────────────────────────────────────────
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    try:
        for i, (sys_msg, usr_msg, max_tok) in enumerate(requests):
            line = {
                "custom_id": f"req-{i}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": use_model,
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": usr_msg},
                    ],
                    "max_tokens": max_tok,
                },
            }
            tmp.write(json.dumps(line, ensure_ascii=False) + "\n")
        tmp.close()

        logger.info(f"[Batch] 提交 {len(requests)} 个请求 (model={use_model})")

        # ── 2. 上传文件 ──────────────────────────────────────────────────
        with open(tmp.name, "rb") as f:
            file_obj = await client.files.create(file=f, purpose="batch")
        logger.info(f"[Batch] 文件已上传: {file_obj.id}")

        # ── 3. 创建 Batch ────────────────────────────────────────────────
        batch = await client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        logger.info(f"[Batch] 批次已创建: {batch.id}")

        # ── 4. 轮询等待完成 ──────────────────────────────────────────────
        poll_interval = settings.batch_poll_interval
        max_wait = settings.batch_max_wait
        elapsed = 0

        while elapsed < max_wait:
            batch = await client.batches.retrieve(batch.id)
            status = batch.status

            if status == "completed":
                logger.info(
                    f"[Batch] 完成: {batch.request_counts.completed}/{batch.request_counts.total} "
                    f"成功, {batch.request_counts.failed} 失败, 耗时 {elapsed}s"
                )
                break
            elif status in ("failed", "expired", "cancelled"):
                logger.error(f"[Batch] 失败: status={status}")
                return [None] * len(requests)

            logger.debug(f"[Batch] 等待中: status={status}, elapsed={elapsed}s")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        if elapsed >= max_wait:
            logger.error(f"[Batch] 超时: 等待超过 {max_wait}s")
            return [None] * len(requests)

        # ── 5. 下载结果 ──────────────────────────────────────────────────
        if not batch.output_file_id:
            logger.error("[Batch] 无输出文件")
            return [None] * len(requests)

        output_content = await client.files.content(batch.output_file_id)
        output_text = output_content.text

        # ── 6. 解析结果 ──────────────────────────────────────────────────
        result_map: dict[str, LLMResponse | None] = {}
        for line in output_text.strip().split("\n"):
            if not line.strip():
                continue
            item = json.loads(line)
            custom_id = item["custom_id"]
            resp_body = item.get("response", {}).get("body", {})

            if item.get("error"):
                logger.warning(f"[Batch] {custom_id} error: {item['error']}")
                result_map[custom_id] = None
                continue

            choices = resp_body.get("choices", [])
            usage = resp_body.get("usage", {})
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                result_map[custom_id] = LLMResponse(
                    content=content,
                    model=resp_body.get("model", use_model),
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
            else:
                result_map[custom_id] = None

        # 按原始顺序返回
        results = []
        total_in = 0
        total_out = 0
        for i in range(len(requests)):
            r = result_map.get(f"req-{i}")
            results.append(r)
            if r:
                total_in += r.input_tokens
                total_out += r.output_tokens

        logger.info(
            f"[Batch] 解析完成: {sum(1 for r in results if r)}/{len(results)} 成功, "
            f"tokens: in={total_in} out={total_out}"
        )
        return results

    except Exception as exc:
        logger.error(f"[Batch] 异常: {exc}")
        # 退化为串行实时调用
        logger.info("[Batch] 退化为串行实时调用")
        results = []
        for sys_msg, usr_msg, max_tok in requests:
            r = await _openai_completion(sys_msg, usr_msg, max_tok, model=model)
            results.append(r)
        return results
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Embedding（节点归一化预筛用）
# ---------------------------------------------------------------------------


async def get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """批量获取文本 embedding 向量。使用 OpenAI 兼容 API。

    Returns:
        与 texts 等长的向量列表，失败返回 None。
    """
    from loguru import logger

    api_key = settings.embedding_api_key or settings.llm_api_key
    base_url = settings.embedding_base_url or settings.llm_base_url or None
    model = settings.embedding_model or "text-embedding-v3"

    if not api_key:
        logger.warning("[Embedding] API key 未配置，跳过 embedding")
        return None

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        resp = await client.embeddings.create(model=model, input=texts)
        vectors = [item.embedding for item in sorted(resp.data, key=lambda x: x.index)]
        logger.debug(f"[Embedding] {len(texts)} texts → {len(vectors[0])}d vectors")
        return vectors
    except Exception as exc:
        logger.warning(f"[Embedding] 失败: {exc}")
        return None


# ---------------------------------------------------------------------------
# OpenAI 兼容后端（Qwen / DeepSeek 等）
# ---------------------------------------------------------------------------


async def _openai_completion(
    system: str, user: str, max_tokens: int, model: str | None = None
) -> Optional[LLMResponse]:
    from openai import AsyncOpenAI, APIError

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
    try:
        resp = await client.chat.completions.create(
            model=model or _get_openai_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )
    except APIError as exc:
        from loguru import logger
        logger.error(f"[LLMClient] OpenAI API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Anthropic 后端
# ---------------------------------------------------------------------------


async def _anthropic_completion(
    system: str, user: str, max_tokens: int, model: str | None = None
) -> Optional[LLMResponse]:
    import anthropic

    api_key = settings.anthropic_api_key
    if not api_key or api_key == "mock":
        from loguru import logger
        logger.error("[LLMClient] ANTHROPIC_API_KEY 未配置")
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        resp = await client.messages.create(
            model=model or _get_anthropic_model(),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return LLMResponse(
            content=resp.content[0].text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
    except anthropic.APIError as exc:
        from loguru import logger
        logger.error(f"[LLMClient] Anthropic API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# OpenAI 视觉（图片理解）
# ---------------------------------------------------------------------------


async def _openai_vision_completion(
    system: str, user: str, image_url: str, max_tokens: int
) -> Optional[LLMResponse]:
    from openai import AsyncOpenAI, APIError

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
    try:
        resp = await client.chat.completions.create(
            model=_get_openai_vision_model(),
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": user},
                    ],
                },
            ],
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )
    except APIError as exc:
        from loguru import logger
        logger.error(f"[LLMClient] OpenAI vision API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Anthropic 视觉（图片理解）
# ---------------------------------------------------------------------------


async def _anthropic_vision_completion(
    system: str, user: str, image_url: str, max_tokens: int
) -> Optional[LLMResponse]:
    import anthropic

    api_key = settings.anthropic_api_key
    if not api_key or api_key == "mock":
        from loguru import logger
        logger.error("[LLMClient] ANTHROPIC_API_KEY 未配置")
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        resp = await client.messages.create(
            model=_get_anthropic_vision_model(),
            max_tokens=max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": image_url},
                        },
                        {"type": "text", "text": user},
                    ],
                }
            ],
        )
        return LLMResponse(
            content=resp.content[0].text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
    except anthropic.APIError as exc:
        from loguru import logger
        logger.error(f"[LLMClient] Anthropic vision API error: {exc}")
        return None
