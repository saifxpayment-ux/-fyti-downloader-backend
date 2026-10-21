
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

app = FastAPI(title="FYTI Downloader API")

# Connect your website here after backend deployment.
# For initial testing, allow browser requests from any origin.
# Restrict this before public production use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

ALLOWED_HOSTS = {
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "tiktok.com",
}

MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


class VideoRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    quality: str = "720p"


def validate_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    if parsed.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="Only HTTPS video URLs are accepted.",
        )

    allowed = any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_HOSTS
    )

    if not allowed:
        raise HTTPException(
            status_code=400,
            detail="This video platform is not supported.",
        )

    return url.strip()


@app.get("/")
def home():
    return {
        "service": "FYTI Downloader API",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/info")
def video_info(request: VideoRequest):
    url = validate_url(request.url)

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 15,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise HTTPException(
                status_code=422,
                detail="Video information is unavailable.",
            )

        return {
            "title": info.get("title") or "Video",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "platform": info.get("extractor_key"),
        }

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not read this video. It may be private, "
                "restricted, or unsupported."
            ),
        )


@app.post("/download")
def download_video(request: VideoRequest):
    url = validate_url(request.url)

    quality_map = {
        "460p": "best[height<=460]/best",
        "480p": "best[height<=480]/best",
        "540p": "best[height<=540]/best",
        "720p": "best[height<=720]/best",
        "1080p": "best[height<=1080]/best",
        "2k": "best[height<=1440]/best",
        "4k": "best[height<=2160]/best",
    }

    quality = request.quality.lower()

    if quality not in quality_map:
        raise HTTPException(
            status_code=400,
            detail="Choose a supported quality.",
        )

    temp_dir = tempfile.mkdtemp(prefix="fyti_")
    folder = Path(temp_dir)

    options = {
        "format": quality_map[quality],
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "socket_timeout": 20,
        "retries": 1,
        "fragment_retries": 1,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared = Path(ydl.prepare_filename(info))

        candidates = [
            p for p in folder.iterdir()
            if p.is_file()
            and not p.name.endswith((".part", ".ytdl"))
        ]

        if prepared.exists() and prepared.stat().st_size > 0:
            output = prepared
        else:
            valid = [
                p for p in candidates
                if p.suffix.lower() in {
                    ".mp4", ".webm", ".mkv", ".mov"
                }
                and p.stat().st_size > 0
            ]

            if not valid:
                raise RuntimeError("Downloaded video file not found.")

            output = max(valid, key=lambda p: p.stat().st_mtime)

        if output.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("File exceeds the 100 MB limit.")

        return FileResponse(
            path=str(output),
            filename=re.sub(
                r"[^A-Za-z0-9._-]",
                "_",
                output.name,
            ),
            media_type="application/octet-stream",
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True,
            ),
        )

    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)

        if isinstance(exc, HTTPException):
            raise exc

        raise HTTPException(
            status_code=422,
            detail=(
                "Download failed. The video may be restricted, "
                "unsupported, or unavailable on this server."
            ),
        )
