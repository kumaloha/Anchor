"""
统一 LLM 客户端
===============
屏蔽 Anthropic SDK 和 OpenAI SDK 的差异，提供统一的 chat_completion 接口。

配置方式（.env）：
  # 使用 Anthropic（默认）
  LLM_PROVIDER=anthropic
  ANTHROPIC_API_KEY=sk-ant-...

  # 使用 OpenAI 兼容接口（Qwen / DeepSeek / 本地 Ollama 等）
  LLM_PROVIDER=openai
  LLM_API_KEY=sk-...
  LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
  LLM_MODEL=qwen-plus
"""

from __future__ import annotations

from dataclasses import dataclass
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
) -> Optional[LLMResponse]:
    """调用 LLM，返回文本响应。失败返回 None。"""
    if _is_openai_mode():
        return await _openai_completion(system, user, max_tokens)
    return await _anthropic_completion(system, user, max_tokens)


async def transcribe_audio(
    audio_path: str,
    language: str | None = None,
) -> str | None:
    """将音频文件转录为文字（Whisper 兼容 API）。

    优先使用 asr_api_key；若未配置，则尝试复用 llm_api_key（OpenAI 模式）。
    两者都未配置时返回 None。

    Args:
        audio_path: 本地音频文件路径（mp3/m4a/webm/wav，≤25 MB）
        language:   语言代码（如 "zh"、"en"）；None 时由 Whisper 自动检测

    Returns:
        转录文本；失败时返回 None
    """
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
# OpenAI 兼容后端（Qwen / DeepSeek 等）
# ---------------------------------------------------------------------------


async def _openai_completion(
    system: str, user: str, max_tokens: int
) -> Optional[LLMResponse]:
    from openai import AsyncOpenAI, APIError

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
    try:
        resp = await client.chat.completions.create(
            model=_get_openai_model(),
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
    system: str, user: str, max_tokens: int
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
            model=_get_anthropic_model(),
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
