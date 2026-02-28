#!/usr/bin/env python3
"""
YouTube 音频下载 + 转录测试脚本
输出：
  - {VIDEO_ID}.m4a        — 音频文件（提取完成后复制到当前目录）
  - youtube_audio_test.log — 完整日志（含转录全文）
"""
from __future__ import annotations

import asyncio
import glob as _glob
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

VIDEO_ID = "mwlfhizB2Q8"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
LOG_FILE = Path("youtube_audio_test.log")
AUDIO_OUT = Path(f"{VIDEO_ID}.m4a")


# ---------------------------------------------------------------------------
# 日志工具
# ---------------------------------------------------------------------------

_log_lines: list[str] = []


def log(msg: str = "", file_only: bool = False):
    """同时写入文件和控制台（file_only=True 仅写文件）。"""
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_lines.append(line)
    if not file_only:
        print(line)


def flush_log():
    LOG_FILE.write_text("\n".join(_log_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 1: yt-dlp 下载原始文件（尝试多种 cookie 来源）
# ---------------------------------------------------------------------------


def _download_raw(tmp_dir: str) -> str | None:
    import yt_dlp

    dl_tmpl = os.path.join(tmp_dir, f"{VIDEO_ID}_raw.%(ext)s")

    # 依次尝试：无 cookies → chrome → firefox → safari
    attempts: list[tuple[str, dict]] = [
        ("无 cookies", {}),
        ("Chrome", {"cookiesfrombrowser": ("chrome", None, None, None)}),
        ("Firefox", {"cookiesfrombrowser": ("firefox", None, None, None)}),
        ("Safari", {"cookiesfrombrowser": ("safari", None, None, None)}),
    ]

    for label, extra in attempts:
        log(f"  尝试 [{label}]...")
        dl_opts: dict = {
            "format": "bestaudio/18/best",
            "outtmpl": dl_tmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            **extra,
        }
        try:
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([VIDEO_URL])
            matches = _glob.glob(os.path.join(tmp_dir, f"{VIDEO_ID}_raw.*"))
            if matches:
                raw_path = matches[0]
                sz = os.path.getsize(raw_path)
                log(f"  ✓ 下载成功 [{label}]  大小={sz//1024} KB  路径={raw_path}")
                return raw_path
            log(f"  ✗ 未找到下载文件 [{label}]")
        except Exception as exc:
            log(f"  ✗ 失败 [{label}]: {exc}")

    return None


# ---------------------------------------------------------------------------
# Step 2: PyAV 抽取音频轨道 → m4a
# ---------------------------------------------------------------------------


def _extract_audio(raw_path: str, out_path: str, max_dur: int) -> bool:
    try:
        import av
    except ImportError:
        log("  ✗ PyAV 未安装")
        return False

    try:
        with av.open(raw_path) as in_c:
            audio_streams = [s for s in in_c.streams if s.type == "audio"]
            if not audio_streams:
                log("  ✗ 文件中无音频轨道")
                return False

            astream = audio_streams[0]
            log(
                f"  codec={astream.codec_context.name}  sample_rate={astream.sample_rate}  max_dur={max_dur}s"
            )

            with av.open(out_path, mode="w", format="ipod") as out_c:
                ostream = out_c.add_stream("aac", rate=16000)
                ostream.layout = "mono"

                for frame in in_c.decode(astream):
                    if max_dur > 0 and frame.time and frame.time > max_dur:
                        log(f"  ⚡ 达到时长限制 {max_dur}s，截断")
                        break
                    frame.pts = None
                    for pkt in ostream.encode(frame):
                        out_c.mux(pkt)

                for pkt in ostream.encode():
                    out_c.mux(pkt)

        sz = os.path.getsize(out_path)
        log(f"  ✓ 音频提取完成: {sz//1024} KB")
        return True

    except Exception as exc:
        log(f"  ✗ PyAV 失败: {exc}")
        return False


# ---------------------------------------------------------------------------
# Step 3: Whisper API 转录
# ---------------------------------------------------------------------------


async def _transcribe_whisper(audio_path: str) -> str | None:
    from anchor.config import settings
    from anchor.llm_client import transcribe_audio

    api_key = settings.asr_api_key or settings.llm_api_key
    if not api_key:
        log("  ⚠ ASR_API_KEY / LLM_API_KEY 均未配置，跳过 Whisper 转录")
        return None

    log(
        f"  model={settings.asr_model}  文件大小={os.path.getsize(audio_path)//1024} KB"
    )
    text = await transcribe_audio(audio_path, language=None)
    if text:
        log(f"  ✓ 转录完成: {len(text)} 字符")
    else:
        log("  ✗ Whisper 转录失败")
    return text


# ---------------------------------------------------------------------------
# Step 4: youtube-transcript-api 字幕（回落）
# ---------------------------------------------------------------------------


async def _fetch_subtitle() -> str | None:
    import re

    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        tl = api.list(VIDEO_ID)
        available = {t.language_code: t for t in tl}
        log(f"  可用字幕语言: {list(available.keys())}")

        for lang in ["zh-Hans", "zh-Hant", "zh", "en"]:
            if lang in available:
                entries = available[lang].fetch()
                text = " ".join(e.text.strip() for e in entries if e.text.strip())
                text = re.sub(r"(\[.*?\])\s*(\1\s*)+", r"\1 ", text)
                log(f"  ✓ 字幕 [{lang}] 获取成功: {len(text)} 字符")
                return text

        if available:
            first = next(iter(available.values()))
            entries = first.fetch()
            text = " ".join(e.text.strip() for e in entries if e.text.strip())
            log(f"  ✓ 字幕 [{first.language_code}] 获取成功: {len(text)} 字符")
            return text

        log("  ✗ 无可用字幕")
        return None

    except Exception as exc:
        log(f"  ✗ 字幕获取失败: {exc}")
        return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def main():
    from anchor.config import settings

    log("=" * 64)
    log(f"YouTube 音频下载 + 转录测试")
    log(f"视频 URL : {VIDEO_URL}")
    log(f"最大时长 : {settings.youtube_max_duration}s (0=不限)")
    log("=" * 64)

    tmp_dir = tempfile.mkdtemp(prefix="anchor_yt_test_")
    raw_path = None
    audio_path = None

    # ── Step 1 ──────────────────────────────────────────────────────
    log("\n>>> Step 1: yt-dlp 下载原始文件")
    raw_path = _download_raw(tmp_dir)

    # ── Step 2 ──────────────────────────────────────────────────────
    if raw_path:
        log("\n>>> Step 2: PyAV 抽取音频轨道")
        tmp_audio = os.path.join(tmp_dir, f"{VIDEO_ID}.m4a")
        ok = _extract_audio(raw_path, tmp_audio, settings.youtube_max_duration)

        # 删除原始文件（可能很大）
        os.remove(raw_path)
        raw_path = None

        if ok:
            # 复制 m4a 到当前目录，方便用户查看
            shutil.copy2(tmp_audio, AUDIO_OUT)
            log(f"  ✓ 音频文件已保存至: {AUDIO_OUT.absolute()}")
            audio_path = tmp_audio
    else:
        log("  (跳过 PyAV，因为下载失败)")

    # ── Step 3 ──────────────────────────────────────────────────────
    transcript_text: str | None = None
    if audio_path:
        log("\n>>> Step 3: Whisper API 转录")
        transcript_text = await _transcribe_whisper(audio_path)

    # ── Step 4 ──────────────────────────────────────────────────────
    if not transcript_text:
        log("\n>>> Step 4: 字幕回落（youtube-transcript-api）")
        transcript_text = await _fetch_subtitle()

    # ── 结果汇总 ─────────────────────────────────────────────────────
    log("\n" + "=" * 64)
    log("转录文本（控制台仅显示前 300 字，完整内容见日志文件）:")
    log("-" * 64)
    if transcript_text:
        preview = transcript_text[:300]
        print(preview)
        log(preview, file_only=True)  # 已经 print 过，避免重复
        log(f"\n... 共 {len(transcript_text)} 字符 ...")
        log("\n[完整转录文本]", file_only=True)
        log(transcript_text, file_only=True)
    else:
        log("（未能获取任何转录文本）")

    log("\n" + "=" * 64)
    if AUDIO_OUT.exists():
        log(f"音频文件: {AUDIO_OUT.absolute()}  ({AUDIO_OUT.stat().st_size//1024} KB)")
    else:
        log("音频文件: 未生成（yt-dlp 下载失败）")
    log(f"日志文件: {LOG_FILE.absolute()}")
    log("=" * 64)

    # 写入日志文件
    flush_log()

    # 清理临时目录
    try:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
