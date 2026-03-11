"""Bilibili 视频采集器
===================
策略：
  Layer A — yt-dlp 下载音频 + Whisper 转录
  Layer B — 仅标题（yt-dlp 失败时回落）

配置与 YouTube 共用：
  ASR_API_KEY / ASR_BASE_URL / ASR_MODEL
  YOUTUBE_MAX_DURATION（复用时长限制）
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone

from loguru import logger

from anchor.collect.base import BaseCollector, RawPostData
from anchor.config import settings

_WHISPER_MAX_BYTES = 24 * 1024 * 1024   # 24 MB

_BV_RE = re.compile(r"BV[\w]+")

# ── yt-dlp cookie 文件（从 BILIBILI_COOKIE 环境变量生成）──────────────────────

_BILI_COOKIE_FILE: str | None = None


def _get_bili_cookie_args() -> list[str]:
    """返回 yt-dlp 的 cookie 参数（--cookies <file>），无 cookie 时返回空列表。"""
    global _BILI_COOKIE_FILE

    cookie_str = os.environ.get("BILIBILI_COOKIE", "")
    if not cookie_str:
        return []

    if _BILI_COOKIE_FILE is None or not os.path.exists(_BILI_COOKIE_FILE):
        import tempfile
        fd, path = tempfile.mkstemp(prefix="bili_cookies_", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for part in cookie_str.split(";"):
                part = part.strip()
                if "=" not in part:
                    continue
                key, _, val = part.partition("=")
                f.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key.strip()}\t{val.strip()}\n")
        _BILI_COOKIE_FILE = path
        logger.info(f"[Bilibili] Wrote Netscape cookie file from BILIBILI_COOKIE env")

    return ["--cookies", _BILI_COOKIE_FILE]


class BilibiliCollector(BaseCollector):
    """Bilibili 视频采集器（yt-dlp + Whisper）。"""

    @property
    def source_name(self) -> str:
        return "bilibili"

    async def collect(self, **kwargs) -> list[RawPostData]:
        return await self.collect_by_ids(kwargs.get("bv_ids", []))

    async def collect_by_ids(self, bv_ids: list[str]) -> list[RawPostData]:
        results = []
        for bv in bv_ids:
            data = await self._fetch_video(bv)
            if data:
                results.append(data)
        return results

    async def _fetch_video(self, bv_id: str) -> RawPostData | None:
        from anchor.collect.youtube import _extract_speaker_from_title

        video_url = f"https://www.bilibili.com/video/{bv_id}"

        # ── 元数据（yt-dlp --dump-json）────────────────────────────
        title, uploader, duration_s = await _fetch_metadata(bv_id, video_url)
        title = title or bv_id

        # 作者优先级：标题里的人名 > 上传者（uploader/channel）
        speaker = _extract_speaker_from_title(title)
        author_name = speaker if speaker else (uploader or bv_id)

        # ── 音频转录 ────────────────────────────────────────────────
        transcript: str | None = None
        method: str | None = None

        has_asr_key = bool(settings.asr_api_key or settings.llm_api_key)
        if has_asr_key:
            transcript, method = await _transcribe_via_audio(bv_id, video_url)
        else:
            logger.debug(f"[Bilibili] {bv_id} 未配置 ASR key，跳过音频转录")

        if transcript:
            logger.info(
                f"[Bilibili] {bv_id} 内容获取成功（方式={method}，{len(transcript)} 字）"
            )
            content = f"# {title}\n\n## 视频内容\n\n{transcript}"
        else:
            logger.warning(f"[Bilibili] {bv_id} 无法获取内容，仅保存标题")
            content = f"# {title}\n\n（无法获取视频内容）"
            method = "title_only"

        return RawPostData(
            source="bilibili",
            external_id=bv_id,
            content=content,
            author_name=author_name,
            author_id=uploader or bv_id,
            url=video_url,
            posted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            metadata={
                "title":             title,
                "uploader":          uploader,
                "duration_s":        duration_s,
                "transcript_method": method,
                "transcript_chars":  len(transcript) if transcript else 0,
            },
            media_items=[],
        )


# ---------------------------------------------------------------------------
# 元数据（yt-dlp --dump-json）
# ---------------------------------------------------------------------------


async def _fetch_metadata(bv_id: str, video_url: str) -> tuple[str | None, str | None, int | None]:
    import asyncio
    import json

    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-playlist",
            "--socket-timeout", "20",
            *_get_bili_cookie_args(),
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if stdout:
            info = json.loads(stdout.decode("utf-8", errors="replace").strip().splitlines()[0])
            title    = info.get("title")
            uploader = info.get("uploader") or info.get("channel")
            duration = info.get("duration")
            return title, uploader, int(duration) if duration else None
    except Exception as exc:
        logger.warning(f"[Bilibili] yt-dlp metadata failed for {bv_id}: {exc}")
    return None, None, None


# ---------------------------------------------------------------------------
# 音频下载（yt-dlp）+ Whisper 转录
# ---------------------------------------------------------------------------


async def _transcribe_via_audio(bv_id: str, video_url: str) -> tuple[str | None, str | None]:
    from anchor.llm_client import transcribe_audio

    tmp_dir = tempfile.mkdtemp(prefix="anchor_bili_")
    audio_path: str | None = None
    try:
        audio_path = await _download_audio(bv_id, video_url, tmp_dir)
        if not audio_path:
            return None, None

        size = os.path.getsize(audio_path)
        if size > _WHISPER_MAX_BYTES:
            logger.warning(f"[Bilibili] 音频 {size//1024//1024} MB 超过 24 MB 限制，跳过 ASR")
            return None, None

        logger.info(f"[Bilibili] 音频下载完成 ({size//1024} KB)，开始 Whisper 转录…")
        text = await transcribe_audio(audio_path, language=None)
        return (text, "whisper") if text else (None, None)

    except Exception as exc:
        logger.warning(f"[Bilibili] 音频转录失败: {exc}")
        return None, None
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def _download_audio(bv_id: str, video_url: str, output_dir: str) -> str | None:
    """用 yt-dlp 下载音频，PyAV 重编码为 16kHz mono m4a。"""
    import asyncio

    max_dur = settings.youtube_max_duration  # 复用同一限制

    raw_path = os.path.join(output_dir, f"{bv_id}_raw.%(ext)s")
    out_path = os.path.join(output_dir, f"{bv_id}.m4a")

    # ── yt-dlp 下载最佳音频流 ─────────────────────────────────────
    try:
        args = [
            "yt-dlp",
            "-f", "bestaudio",
            "--no-playlist",
            "--socket-timeout", "30",
            *_get_bili_cookie_args(),
            "-o", raw_path,
            "--quiet",
            video_url,
        ]
        if max_dur > 0:
            args += ["--download-sections", f"*0-{max_dur}"]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            logger.warning(f"[Bilibili] yt-dlp 下载失败 (rc={proc.returncode}): {stderr.decode()[:600]}")
            return None
    except Exception as exc:
        logger.warning(f"[Bilibili] yt-dlp 异常: {exc}")
        return None

    # 找到实际下载的文件（扩展名不定）
    downloaded = None
    for f in os.listdir(output_dir):
        if f.startswith(f"{bv_id}_raw."):
            downloaded = os.path.join(output_dir, f)
            break
    if not downloaded or not os.path.exists(downloaded):
        logger.warning(f"[Bilibili] 找不到下载的音频文件")
        return None

    sz = os.path.getsize(downloaded)
    logger.info(f"[Bilibili] yt-dlp 下载完成: {sz//1024} KB")

    # ── PyAV 重编码 → 16kHz mono m4a ────────────────────────────
    try:
        import av

        with av.open(downloaded) as in_c:
            audio_streams = [s for s in in_c.streams if s.type == "audio"]
            if not audio_streams:
                logger.warning("[Bilibili] 下载文件中无音频轨道")
                return None
            astream = audio_streams[0]

            with av.open(out_path, mode="w", format="ipod") as out_c:
                ostream = out_c.add_stream("aac", rate=16000)
                ostream.layout = "mono"

                for frame in in_c.decode(astream):
                    if max_dur > 0 and frame.time and frame.time > max_dur:
                        break
                    frame.pts = None
                    for pkt in ostream.encode(frame):
                        out_c.mux(pkt)

                for pkt in ostream.encode():
                    out_c.mux(pkt)

        sz = os.path.getsize(out_path)
        logger.info(f"[Bilibili] 重编码完成: {sz//1024} KB")
        return out_path

    except Exception as exc:
        logger.warning(f"[Bilibili] PyAV 重编码失败: {exc}")
        if os.path.exists(out_path):
            os.remove(out_path)
        return None
    finally:
        if os.path.exists(downloaded):
            os.remove(downloaded)
