import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

import yt_dlp
from faster_whisper import WhisperModel

from app.core.config import settings
from app.models.blogger import Blogger
from app.models.raw_content import ContentTypeEnum, RawContent
from app.services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

_whisper_model: Optional[Any] = None


def _get_whisper_model() -> Any:
    """Lazy-load the whisper model (expensive, do once)."""
    global _whisper_model
    if _whisper_model is None:
        logger.info("Loading Whisper model '%s'...", settings.WHISPER_MODEL)
        _whisper_model = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model


def _is_channel_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path
    return any(
        path.startswith(prefix)
        for prefix in ("/channel/", "/c/", "/user/", "/@")
    )


def _is_video_url(url: str) -> bool:
    parsed = urlparse(url)
    if "youtube.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        return "v" in qs
    if "youtu.be" in parsed.netloc:
        return bool(parsed.path.strip("/"))
    return False


def _fetch_video_info(url: str) -> Optional[Dict]:
    """Fetch video metadata (no download) using yt-dlp."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except yt_dlp.utils.DownloadError as exc:
        logger.error("yt-dlp: failed to fetch video info for %s: %s", url, exc)
        return None


def _fetch_channel_recent_videos(channel_url: str, max_videos: int) -> List[Dict]:
    """Fetch the N most recent video entries from a channel."""
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": max_videos,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            if info and "entries" in info:
                entries = list(info["entries"])[:max_videos]
                return entries
            return []
    except yt_dlp.utils.DownloadError as exc:
        logger.error("yt-dlp: failed to fetch channel %s: %s", channel_url, exc)
        return []


def _transcribe_video(video_url: str) -> Optional[str]:
    """Download audio from a YouTube video and transcribe with Whisper."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.%(ext)s")
        ydl_opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": audio_path,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "96",
                }
            ],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except yt_dlp.utils.DownloadError as exc:
            logger.error("yt-dlp: audio download failed for %s: %s", video_url, exc)
            return None

        # Find the downloaded mp3
        mp3_file = os.path.join(tmpdir, "audio.mp3")
        if not os.path.exists(mp3_file):
            # Try any audio file present
            candidates = [f for f in os.listdir(tmpdir) if f.startswith("audio")]
            if not candidates:
                logger.error("No audio file found in %s", tmpdir)
                return None
            mp3_file = os.path.join(tmpdir, candidates[0])

        try:
            model = _get_whisper_model()
            segments, _ = model.transcribe(mp3_file, beam_size=5)
            text = " ".join(seg.text for seg in segments).strip()
            return text
        except Exception as exc:
            logger.error("Whisper transcription failed for %s: %s", video_url, exc)
            return None


def _parse_published_at(info: Dict) -> Optional[datetime]:
    upload_date = info.get("upload_date")  # format: YYYYMMDD
    if upload_date and len(upload_date) == 8:
        try:
            dt = datetime(
                int(upload_date[:4]),
                int(upload_date[4:6]),
                int(upload_date[6:8]),
                tzinfo=timezone.utc,
            )
            return dt
        except ValueError:
            pass
    timestamp = info.get("timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        except (ValueError, OSError):
            pass
    return None


class YouTubeCollector(BaseCollector):
    """Collector for YouTube videos using yt-dlp and openai-whisper."""

    def supports_platform(self, platform: str) -> bool:
        return platform == "youtube"

    async def fetch(self, blogger: Blogger) -> List[RawContent]:
        url = blogger.url or ""
        if not url:
            logger.warning("YouTubeCollector: blogger '%s' has no URL", blogger.name)
            return []

        if _is_video_url(url):
            return await self._process_single_video(url, blogger)
        elif _is_channel_url(url) or "youtube.com" in url or "youtu.be" in url:
            return await self._process_channel(url, blogger)
        else:
            logger.warning("YouTubeCollector: unrecognized YouTube URL: %s", url)
            return []

    async def _process_single_video(self, video_url: str, blogger: Blogger) -> List[RawContent]:
        info = _fetch_video_info(video_url)
        if not info:
            return []

        rc = self._build_raw_content(info, video_url, blogger)
        return [rc]

    async def _process_channel(self, channel_url: str, blogger: Blogger) -> List[RawContent]:
        entries = _fetch_channel_recent_videos(channel_url, settings.YT_MAX_RECENT_VIDEOS)
        results: List[RawContent] = []

        for entry in entries:
            video_id = entry.get("id") or entry.get("url")
            if not video_id:
                continue
            video_url = f"https://www.youtube.com/watch?v={video_id}" if not video_id.startswith("http") else video_id
            info = _fetch_video_info(video_url)
            if not info:
                continue
            rc = self._build_raw_content(info, video_url, blogger)
            results.append(rc)

        logger.info(
            "YouTubeCollector: fetched %d videos for blogger '%s'",
            len(results),
            blogger.name,
        )
        return results

    def _build_raw_content(self, info: Dict, video_url: str, blogger: Blogger) -> RawContent:
        title = info.get("title", "")
        description = info.get("description", "")
        raw_text = f"{title}\n\n{description}".strip()

        # Transcribe the video
        transcript = _transcribe_video(video_url)

        published_at = _parse_published_at(info)
        video_id = info.get("id", "")

        return RawContent(
            blogger_id=blogger.id,
            platform="youtube",
            content_type=ContentTypeEnum.video,
            raw_text=raw_text,
            video_url=video_url,
            transcript=transcript,
            source_url=video_url,
            source_id=video_id,
            published_at=published_at,
            is_processed=False,
        )
