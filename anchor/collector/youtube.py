"""YouTube 视频采集器
===================
三层内容获取策略（按优先级）：

  Layer A — 字幕（youtube-transcript-api）
    最快、最准确，适用于有手动或自动字幕的视频。

  Layer B — 音频转录（pytubefix + PyAV + Whisper）
    当视频无字幕时启用。
    pytubefix 无需 PO Token，直接访问 YouTube 内部 API。
    PyAV（内嵌 FFmpeg）从下载文件中抽取音频，重编码为 16kHz mono m4a。
    Whisper 兼容 API 完成转录。
    需配置 ASR_API_KEY 或 LLM_API_KEY。

  Layer C — 仅标题
    字幕和音频均不可用时的最后回落。

配置项（.env）：
  ASR_API_KEY=sk-...               # Whisper 兼容 Key（不填则复用 LLM_API_KEY）
  ASR_BASE_URL=                    # 可选，替换为 Groq 等端点
  ASR_MODEL=whisper-1              # 默认 whisper-1；Groq 用 whisper-large-v3-turbo
  YOUTUBE_MAX_DURATION=1800        # 最长转录时长（秒），0=不限制，默认 30 分钟

支持的 URL 格式：
  https://www.youtube.com/watch?v=VIDEO_ID
  https://youtu.be/VIDEO_ID
  https://youtube.com/shorts/VIDEO_ID
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone

import httpx
from loguru import logger

from anchor.collector.base import BaseCollector, RawPostData
from anchor.config import settings


_YT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Whisper API 单文件上传上限（字节）
_WHISPER_MAX_BYTES = 24 * 1024 * 1024   # 24 MB 留一点余量


class YouTubeCollector(BaseCollector):
    """YouTube 视频采集器。"""

    @property
    def source_name(self) -> str:
        return "youtube"

    async def collect(self, **kwargs) -> list[RawPostData]:
        return await self.collect_by_ids(kwargs.get("video_ids", []))

    async def collect_by_ids(self, video_ids: list[str]) -> list[RawPostData]:
        results = []
        async with httpx.AsyncClient(timeout=20, headers=_YT_HEADERS) as client:
            for vid in video_ids:
                data = await self._fetch_video(vid, client)
                if data:
                    results.append(data)
        return results

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def _fetch_video(
        self, video_id: str, client: httpx.AsyncClient
    ) -> RawPostData | None:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        # ── 元数据 ────────────────────────────────────────────────────
        title, author_name, channel_id, duration_s = await self._fetch_metadata(
            video_id, client
        )
        title       = title or video_id
        author_name = author_name or "Unknown"
        channel_id  = channel_id or video_id

        # ── Layer A: 字幕 ─────────────────────────────────────────────
        transcript, method = await self._fetch_subtitle(video_id)

        # ── Layer B: 音频转录（无字幕时） ────────────────────────────
        if not transcript:
            has_asr_key = bool(settings.asr_api_key or settings.llm_api_key)
            if has_asr_key:
                transcript, method = await self._transcribe_via_audio(video_id)
            else:
                logger.debug(
                    f"[YouTube] video_id={video_id} 无字幕且未配置 ASR key，跳过音频转录"
                )

        # ── Layer C: 仅标题 ──────────────────────────────────────────
        if transcript:
            logger.info(
                f"[YouTube] video_id={video_id} 内容获取成功"
                f"（方式={method}，{len(transcript)} 字符）"
            )
            content = f"# {title}\n\n## 视频内容\n\n{transcript}"
        else:
            logger.warning(f"[YouTube] video_id={video_id} 无法获取内容，仅保存标题")
            content = f"# {title}\n\n（无法获取视频内容）"
            method = "title_only"

        return RawPostData(
            source="youtube",
            external_id=video_id,
            content=content,
            author_name=author_name,
            author_id=channel_id,
            url=video_url,
            posted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            metadata={
                "title":             title,
                "channel_id":        channel_id,
                "duration_s":        duration_s,
                "transcript_method": method,
                "transcript_chars":  len(transcript) if transcript else 0,
            },
            media_items=[],
        )

    # ------------------------------------------------------------------
    # Layer A: youtube-transcript-api 字幕
    # ------------------------------------------------------------------

    async def _fetch_subtitle(
        self, video_id: str
    ) -> tuple[str | None, str | None]:
        """从 YouTube 获取已有字幕文本。"""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi

            api = YouTubeTranscriptApi()
            try:
                tl = api.list(video_id)
            except Exception as e:
                logger.debug(f"[YouTube] 无法列出字幕: {e}")
                return None, None

            available = {t.language_code: t for t in tl}
            preference = ["zh-Hans", "zh-Hant", "zh", "en"]
            transcript = None
            for lang in preference:
                if lang in available:
                    transcript = available[lang]
                    break
            if transcript is None and available:
                transcript = next(iter(available.values()))
            if transcript is None:
                return None, None

            entries = transcript.fetch()
            text = " ".join(e.text.strip() for e in entries if e.text.strip())
            text = re.sub(r"(\[.*?\])\s*(\1\s*)+", r"\1 ", text)
            return text, f"subtitle_{transcript.language_code}"

        except ImportError:
            return None, None
        except Exception as exc:
            logger.debug(f"[YouTube] 字幕获取失败: {exc}")
            return None, None

    # ------------------------------------------------------------------
    # Layer B: pytubefix 下载音频 + Whisper 转录
    # ------------------------------------------------------------------

    async def _transcribe_via_audio(
        self, video_id: str
    ) -> tuple[str | None, str | None]:
        """下载音频并调用 Whisper API 转录。"""
        from anchor.llm_client import transcribe_audio

        tmp_dir = tempfile.mkdtemp(prefix="anchor_yt_")
        audio_path: str | None = None
        try:
            audio_path = await _download_audio(video_id, tmp_dir)
            if not audio_path:
                return None, None

            size = os.path.getsize(audio_path)
            if size > _WHISPER_MAX_BYTES:
                logger.warning(
                    f"[YouTube] 音频文件 {size//1024//1024} MB 超过 24 MB 限制，跳过 ASR"
                )
                return None, None

            logger.info(
                f"[YouTube] 音频下载完成 ({size//1024} KB)，开始 Whisper 转录…"
            )
            text = await transcribe_audio(audio_path, language=None)
            if text:
                return text, "whisper"
            return None, None

        except Exception as exc:
            logger.warning(f"[YouTube] 音频转录失败: {exc}")
            return None, None
        finally:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------

    async def _fetch_metadata(
        self, video_id: str, client: httpx.AsyncClient
    ) -> tuple[str | None, str | None, str | None, int | None]:
        """返回 (title, author_name, channel_id, duration_s)。"""
        # 先试 pytubefix（最准确，复用内部 API）
        try:
            import asyncio
            from pytubefix import YouTube

            def _get_info():
                yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
                return yt.title, yt.author, yt.channel_id, yt.length

            loop = asyncio.get_event_loop()
            title, author, channel_id, length = await loop.run_in_executor(None, _get_info)
            return title, author, channel_id, length
        except Exception:
            pass

        # 回落：页面解析
        try:
            resp = await client.get(
                f"https://www.youtube.com/watch?v={video_id}",
                follow_redirects=True,
            )
            html = resp.text
            title_m  = re.search(r'"title"\s*:\s*"([^"]+)"', html)
            author_m = re.search(r'"author"\s*:\s*"([^"]+)"', html)
            cid_m    = re.search(r'"channelId"\s*:\s*"([^"]+)"', html)
            dur_m    = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
            title = title_m.group(1) if title_m else None
            if title:
                title = title.replace("&amp;", "&").replace("&quot;", '"')
            return (
                title,
                author_m.group(1) if author_m else None,
                cid_m.group(1) if cid_m else video_id,
                int(dur_m.group(1)) if dur_m else None,
            )
        except Exception as exc:
            logger.warning(f"[YouTube] 元数据获取失败: {exc}")
            return None, None, video_id, None


# ---------------------------------------------------------------------------
# 音频获取：pytubefix 下载 + PyAV 重编码
# ---------------------------------------------------------------------------


async def _download_audio(video_id: str, output_dir: str) -> str | None:
    """
    用 pytubefix 下载音频并重编码为 16kHz mono m4a。

    流程：
      1. pytubefix 获取音频专用流并下载（无需 PO Token，直接访问 YouTube 内部 API）
      2. PyAV 从下载文件中抽取音频轨道 → 重编码为 16kHz mono AAC m4a
         - 只处理前 YOUTUBE_MAX_DURATION 秒，自然限制 Whisper 消耗
         - 最终文件通常 < 5 MB（30 分钟内容）
      3. 删除原始下载文件，只保留处理后的 m4a
    """
    import asyncio

    def _run() -> str | None:
        max_dur  = settings.youtube_max_duration
        raw_path: str | None = None

        # ── Step 1: pytubefix 下载音频流 ─────────────────────────────
        try:
            from pytubefix import YouTube

            yt     = YouTube(f"https://www.youtube.com/watch?v={video_id}")
            stream = yt.streams.get_audio_only()
            if not stream:
                logger.warning(f"[YouTube] pytubefix: 无音频流")
                return None

            raw_path = stream.download(
                output_path=output_dir,
                filename=f"{video_id}_raw.m4a",
            )
            sz = os.path.getsize(raw_path)
            logger.info(f"[YouTube] pytubefix 下载完成: {sz//1024} KB")
        except Exception as exc:
            logger.warning(f"[YouTube] pytubefix 下载失败: {exc}")
            return None

        # ── Step 2: PyAV 重编码 → 16kHz mono m4a ─────────────────────
        out_path = os.path.join(output_dir, f"{video_id}.m4a")
        try:
            import av

            with av.open(raw_path) as in_c:
                audio_streams = [s for s in in_c.streams if s.type == "audio"]
                if not audio_streams:
                    logger.warning("[YouTube] 下载文件中无音频轨道")
                    return None
                astream = audio_streams[0]

                logger.info(
                    f"[YouTube] 开始重编码音频"
                    f"（codec={astream.codec_context.name}"
                    f" sr={astream.sample_rate}"
                    f" 最长={max_dur}s）"
                )

                with av.open(out_path, mode="w", format="ipod") as out_c:
                    ostream = out_c.add_stream("aac", rate=16000)
                    ostream.layout = "mono"

                    for frame in in_c.decode(astream):
                        if max_dur > 0 and frame.time and frame.time > max_dur:
                            logger.debug(f"[YouTube] 达到时长限制 {max_dur}s，截断")
                            break
                        frame.pts = None
                        for pkt in ostream.encode(frame):
                            out_c.mux(pkt)

                    for pkt in ostream.encode():
                        out_c.mux(pkt)

            sz = os.path.getsize(out_path)
            logger.info(f"[YouTube] 重编码完成: {sz//1024} KB → {out_path}")
            return out_path

        except Exception as exc:
            logger.warning(f"[YouTube] PyAV 重编码失败: {exc}")
            if os.path.exists(out_path):
                os.remove(out_path)
            return None

        finally:
            if raw_path and os.path.exists(raw_path):
                os.remove(raw_path)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)
