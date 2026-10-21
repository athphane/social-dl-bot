"""
Core downloader logic — adapted from the userbot's video_dl.py.
Downloads TikTok, Instagram Reels, YouTube Shorts (and more) via yt-dlp.
"""

import asyncio
import json
import os
import re
import logging
import shutil
import tempfile

import aiohttp

from bot.instagram import download_instagram_photo, run_process

LOGGER = logging.getLogger(__name__)

# User Agent for requests and yt-dlp
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# --- URL Regex patterns ---

# YouTube Shorts
YOUTUBE_SHORTS_REGEX = (
    r'https?://(www\.)?youtube\.com/shorts/[a-zA-Z0-9_-]+/?(?:\?.*)?'
)

# General YouTube (for /dl command, NOT auto-downloaded)
YOUTUBE_REGEX = (
    r'https?://(www\.)?'
    r'(youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)'
    r'[a-zA-Z0-9_-]+/?(?:\?.*)?'
)

# Instagram (reels, posts, stories, share links — also ddinstagram.com)
INSTAGRAM_REGEX = (
    r'https?://(?:www\.)?'
    r'(?:instagram\.com|ddinstagram\.com|kkinstagram\.com)/'
    r'(?:p|reels|reel|tv|stories|share/reel|share/p)/'
    r'[a-zA-Z0-9_-]+/?'
)

# TikTok (tiktok.com, vm.tiktok.com, vt.tiktok.com, t.tiktok.com)
TIKTOK_REGEX = (
    r'https?://(?:www\.|vm\.|vt\.|t\.)?tiktok\.com/'
    r'(?:@[\w.-]*/video/\d+|@/video/\d+|[\w]+/?).*'
)

# Facebook videos
FACEBOOK_REGEX = (
    r'https?://(?:www\.|m\.|web\.)?facebook\.com/'
    r'(?:watch/?\?v=\d+|[\w.-]+/videos/\d+|reel/\d+|'
    r'share/(?:v|r)/\d+|[\w.-]+/posts/\d+)/?.*'
)

# Combined: what we auto-download when a link is spotted in a group
AUTO_DOWNLOAD_REGEX = (
    f"(?:{INSTAGRAM_REGEX}|{TIKTOK_REGEX}|{YOUTUBE_SHORTS_REGEX})"
)

# Combined: everything supported (for the /dl command)
ALL_PLATFORMS_REGEX = (
    f"(?:{INSTAGRAM_REGEX}|{TIKTOK_REGEX}|{YOUTUBE_SHORTS_REGEX}|"
    f"{YOUTUBE_REGEX}|{FACEBOOK_REGEX})"
)


async def follow_redirects(url):
    """Follow HTTP redirects to get the final URL."""
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.head(url, allow_redirects=True) as response:
                if response.status == 200:
                    return str(response.url)
    except Exception:
        pass
    return url


async def normalise_url(url):
    """Process a URL: follow redirects, fix front-end Instagram mirrors."""
    if "youtube.com" in url or "youtu.be" in url:
        return url

    real_url = await follow_redirects(url)

    if "ddinstagram.com" in real_url or "kkinstagram.com" in real_url:
        return real_url.replace("ddinstagram.com", "instagram.com") \
                       .replace("kkinstagram.com", "instagram.com")

    return real_url


def detect_platform(url):
    """Return (platform_name, match_object) or (None, None)."""
    patterns = [
        ("Instagram", INSTAGRAM_REGEX),
        ("TikTok", TIKTOK_REGEX),
        ("YouTube Shorts", YOUTUBE_SHORTS_REGEX),
        ("YouTube", YOUTUBE_REGEX),
        ("Facebook", FACEBOOK_REGEX),
    ]
    for name, pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return name, match
    return None, None


async def prepare_telegram_video(video_path):
    """Produce a fast-start H.264/AAC MP4, copying compatible streams."""
    code, stdout, stderr = await run_process(
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", video_path,
    )
    if code:
        raise RuntimeError(f"Video inspection failed: {stderr.decode(errors='replace')[:400]}")
    streams = json.loads(stdout)["streams"]
    video = next((s for s in streams if s["codec_type"] == "video"), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    if video is None:
        raise RuntimeError("Downloaded file contains no video stream.")

    # Use a separate path even when the input already has an MP4 extension.
    output = os.path.join(os.path.dirname(video_path), "telegram.mp4")
    args = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", video_path,
            "-map", "0:v:0", "-map", "0:a:0?"]
    if video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p":
        args.extend(["-c:v", "copy"])
    else:
        args.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "23",
                     "-pix_fmt", "yuv420p", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"])
    args.extend(["-c:a", "copy" if audio and audio.get("codec_name") == "aac" else "aac",
                 "-movflags", "+faststart", output])
    code, _, stderr = await run_process(*args)
    if code or not os.path.isfile(output) or not os.path.getsize(output):
        raise RuntimeError(f"MP4 conversion failed: {stderr.decode(errors='replace')[:400]}")
    return output


async def download_video(url, socks5_proxy=None):
    """
    Download a video from a supported platform.

    Returns media metadata, including media_type (video/photo) and optional fallback_image_path.
    """
    download_url = await normalise_url(url)

    platform, match = detect_platform(download_url)
    if not platform:
        return {"success": False, "error": "Unsupported URL or platform.", "file_path": None, "title": None, "platform": None}

    temp_dir = tempfile.mkdtemp(prefix="socialdl_")

    try:
        args = [
            "yt-dlp",
            "--user-agent", USER_AGENT,
            download_url,
            "-o", os.path.join(temp_dir, "%(title)s [%(id)s].%(ext)s"),
        ]
        if platform in {"YouTube", "YouTube Shorts"}:
            args.extend(["--no-playlist", "-S", "vcodec:h264,acodec:aac"])
        if socks5_proxy:
            args.extend(["--proxy", socks5_proxy])

        returncode, stdout, stderr = await run_process(*args)

        if returncode != 0 and b"CERTIFICATE_VERIFY_FAILED" in stderr:
            # Only certificate errors can benefit from this retry. Repeating a
            # rate-limited request immediately makes the YouTube block worse.
            args.append("--no-check-certificate")
            returncode, stdout, stderr = await run_process(*args)

        if returncode != 0:
            details = stderr.decode("utf-8", errors="replace")
            if "HTTP Error 429" in details or "Too Many Requests" in details:
                raise RuntimeError(
                    "YouTube is rate-limiting this server (HTTP 429). Please try again later."
                    if platform in {"YouTube", "YouTube Shorts"} else
                    "This platform is rate-limiting this server (HTTP 429). Please try again later."
                )
            # Report the actual error, rather than truncating introductory warnings.
            errors = [line.removeprefix("ERROR: ") for line in details.splitlines()
                      if line.startswith("ERROR:")]
            raise RuntimeError((errors[-1] if errors else details)[-500:])

        downloaded_files = [name for name in os.listdir(temp_dir)
                            if os.path.splitext(name)[1].lower() in
                            {'.mp4', '.mkv', '.webm', '.mov', '.m4v', '.avi'}]
        if not downloaded_files:
            raise RuntimeError("No video files were downloaded.")

        video_path = os.path.join(temp_dir, downloaded_files[0])
        file_name = os.path.splitext(downloaded_files[0])[0]

        # Extract clean title
        if "[" in file_name and "]" in file_name:
            title = file_name.split(" [")[0]
        else:
            title = file_name

        if platform in {"YouTube", "YouTube Shorts"}:
            video_path = await prepare_telegram_video(video_path)

        return {
            "success": True,
            "media_type": "video",
            "file_path": video_path,
            "title": title,
            "platform": platform,
            "download_url": download_url,
            "temp_dir": temp_dir,
        }

    except asyncio.CancelledError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as e:
        error = "Download timed out (300s limit)." if isinstance(e, asyncio.TimeoutError) else str(e)[:500]
        if platform == "Instagram":
            try:
                photo = await download_instagram_photo(download_url, temp_dir, socks5_proxy, USER_AGENT)
                if photo:
                    return photo
            except asyncio.CancelledError:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
            except Exception:
                LOGGER.info("Instagram photo fallback failed", exc_info=True)
        return {"success": False, "error": error, "file_path": None, "title": None,
                "platform": platform, "temp_dir": temp_dir}
