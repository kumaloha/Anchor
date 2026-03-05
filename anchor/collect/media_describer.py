"""
媒体描述器 — Layer 1 Step 1.5
==============================
对帖子中的图片和视频生成文字描述，以便 Layer2 提取器将媒体内容纳入分析。

支持的媒体类型：
  - photo / gif — 调用视觉 LLM，生成图片内容描述
  - video       — 提取音频轨道 → Whisper 转录（与 YouTube Layer B 相同路径）
                  若转录失败则静默跳过

设计原则：
  - 每张图片单独调用一次视觉模型，结果按序合并
  - 视频优先尝试直接下载音频；无法下载时静默降级
  - 若视觉/ASR 模型未配置或调用失败，静默返回 None（不阻断流程）
"""

from __future__ import annotations

import json
import os
import re
import tempfile

from loguru import logger

from anchor.llm_client import chat_completion_multimodal
from anchor.models import RawPost


_IMAGE_SYSTEM = """\
你是一名内容分析助手，专门解读图片中的信息。
请详细描述图片内容，重点关注：
- 文字信息（标题、说明、注解、数字）
- 图表数据（折线图、柱状图、饼图、表格中的数值和趋势）
- 截图内容（新闻截图、公告、财报页面的关键数字）
- 任何与经济、金融、政策相关的可见信息

用中文输出纯文本描述，不加任何前缀或格式标记，不说"这张图片显示"等套话，直接陈述内容。
"""

_IMAGE_PROMPT = "请提取并描述这张图片中的所有关键信息。"

# Whisper 单文件上传上限（字节）
_WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24 MB


async def describe_media(post: RawPost) -> str | None:
    """对帖子中的图片/视频生成文字描述。

    返回合并后的描述字符串（如 "[图1] ... \n\n[视频转录] ..."），
    无媒体或全部失败时返回 None。
    """
    if not post.media_json:
        return None

    try:
        items: list[dict] = json.loads(post.media_json)
    except Exception:
        return None

    descriptions: list[tuple[str, str]] = []  # (label, text)

    photo_items = [item for item in items if item.get("type") in ("photo", "gif")]
    video_items = [item for item in items if item.get("type") == "video"]

    # ── 图片描述 ──────────────────────────────────────────────────────────────
    photo_idx = 1
    for item in photo_items:
        url = item.get("url")
        if not url:
            continue
        logger.info(f"[MediaDescriber] 描述图片 {photo_idx}/{len(photo_items)}: {url[:80]}")
        resp = await chat_completion_multimodal(
            system=_IMAGE_SYSTEM,
            user=_IMAGE_PROMPT,
            image_url=url,
            max_tokens=600,
        )
        if resp and resp.content.strip():
            label = f"图{photo_idx}" if len(photo_items) > 1 else "图片"
            descriptions.append((label, resp.content.strip()))
            logger.debug(
                f"[MediaDescriber] 图片 {photo_idx} 描述完成 "
                f"(in={resp.input_tokens} out={resp.output_tokens})"
            )
        else:
            logger.warning(f"[MediaDescriber] 图片 {photo_idx} 描述失败: {url[:80]}")
        photo_idx += 1

    # ── 视频转录 ──────────────────────────────────────────────────────────────
    video_idx = 1
    for item in video_items:
        url = item.get("url")
        if not url:
            continue
        logger.info(
            f"[MediaDescriber] 转录视频 {video_idx}/{len(video_items)}: {url[:80]}"
        )
        transcript = await _transcribe_video(url)
        if transcript:
            label = f"视频{video_idx}" if len(video_items) > 1 else "视频"
            descriptions.append((label, f"[视频转录]\n{transcript}"))
            logger.info(
                f"[MediaDescriber] 视频 {video_idx} 转录完成 ({len(transcript)} 字符)"
            )
        else:
            logger.warning(f"[MediaDescriber] 视频 {video_idx} 转录失败: {url[:80]}")
        video_idx += 1

    if not descriptions:
        return None

    if len(descriptions) == 1:
        label, text = descriptions[0]
        return f"[{label}] {text}"

    return "\n\n".join(f"[{label}] {text}" for label, text in descriptions)


# ---------------------------------------------------------------------------
# 视频音频提取 + Whisper 转录
# ---------------------------------------------------------------------------


async def _transcribe_video(video_url: str) -> str | None:
    """从视频 URL 提取音频并调用 Whisper 转录。

    策略：
      1. httpx 直接下载视频 URL（适合 Twitter CDN 直链）
      2. PyAV 抽取音频轨道，重编码为 16kHz mono m4a
      3. Whisper API 转录
    """
    from anchor.config import settings
    from anchor.llm_client import transcribe_audio

    has_asr = bool(settings.asr_api_key or settings.llm_api_key)
    if not has_asr:
        logger.debug("[MediaDescriber] 未配置 ASR key，跳过视频转录")
        return None

    tmp_dir = tempfile.mkdtemp(prefix="anchor_vid_")
    audio_path: str | None = None
    try:
        audio_path = await _download_and_extract_audio(video_url, tmp_dir)
        if not audio_path:
            return None

        size = os.path.getsize(audio_path)
        if size > _WHISPER_MAX_BYTES:
            logger.warning(
                f"[MediaDescriber] 音频文件 {size // 1024 // 1024} MB 超过限制，跳过"
            )
            return None

        logger.info(f"[MediaDescriber] 开始 Whisper 转录 ({size // 1024} KB)…")
        text = await transcribe_audio(audio_path, language=None)
        return text or None

    except Exception as exc:
        logger.warning(f"[MediaDescriber] 视频转录流程异常: {exc}")
        return None
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def _download_and_extract_audio(video_url: str, output_dir: str) -> str | None:
    """下载视频并提取音频轨道，返回 m4a 文件路径。"""
    import asyncio

    def _run() -> str | None:
        # Step 1: 下载视频
        raw_path = os.path.join(output_dir, "raw_video")
        try:
            import httpx

            # 生成安全文件名（截取 URL 最后部分，去除查询参数）
            clean = re.sub(r"\?.*$", "", video_url)
            ext = os.path.splitext(clean)[-1].lower() or ".mp4"
            raw_path = os.path.join(output_dir, f"raw{ext}")

            with httpx.Client(
                timeout=30,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                },
            ) as client:
                resp = client.get(video_url)
                resp.raise_for_status()
                with open(raw_path, "wb") as f:
                    f.write(resp.content)

            sz = os.path.getsize(raw_path)
            logger.info(f"[MediaDescriber] 视频下载完成: {sz // 1024} KB")
        except Exception as exc:
            logger.warning(f"[MediaDescriber] 视频下载失败: {exc}")
            return None

        # Step 2: PyAV 抽取音频轨道 → 16kHz mono m4a
        out_path = os.path.join(output_dir, "audio.m4a")
        try:
            import av

            with av.open(raw_path) as in_c:
                audio_streams = [s for s in in_c.streams if s.type == "audio"]
                if not audio_streams:
                    logger.warning("[MediaDescriber] 下载文件中无音频轨道")
                    return None

                astream = audio_streams[0]
                logger.info(
                    f"[MediaDescriber] 重编码音频"
                    f"（codec={astream.codec_context.name}"
                    f" sr={astream.sample_rate}）"
                )

                with av.open(out_path, mode="w", format="ipod") as out_c:
                    ostream = out_c.add_stream("aac", rate=16000)
                    ostream.layout = "mono"

                    for frame in in_c.decode(astream):
                        frame.pts = None
                        for pkt in ostream.encode(frame):
                            out_c.mux(pkt)
                    for pkt in ostream.encode():
                        out_c.mux(pkt)

            sz = os.path.getsize(out_path)
            logger.info(f"[MediaDescriber] 音频重编码完成: {sz // 1024} KB")
            return out_path

        except ImportError:
            logger.debug("[MediaDescriber] PyAV 未安装，无法提取音频")
            return None
        except Exception as exc:
            logger.warning(f"[MediaDescriber] PyAV 重编码失败: {exc}")
            if os.path.exists(out_path):
                os.remove(out_path)
            return None
        finally:
            if os.path.exists(raw_path):
                os.remove(raw_path)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
