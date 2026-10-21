import logging
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

# Logging: show actual errors in Render logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fyti")

app = FastAPI(title="FYTI Downloader API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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

QUALITY_MAP = {
    "460p": "best[height<=460][ext=mp4]/best[height<=460]/best",
    "480p": "best[height<=480][ext=mp4]/best[height<=480]/best",
    "540p": "best[height<=540][ext=mp4]/best[height<=540]/best",
    "720p": "best[height<=720][ext=mp4]/best[height<=720]/best",
    "1080p": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
    "2k": "best[height<=1440][ext=mp4]/best[height<=1440]/best",
    "4k": "best[height<=2160][ext=mp4]/best[height<=2160]/best",
}


class VideoRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    quality: str = "720p"


def validate_url(url: str) -> str:
    url = url.strip()

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid URL.")

    if parsed.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="Only HTTPS video URLs are accepted.",
        )

    if not any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_HOSTS
    ):
        raise HTTPException(
            status_code=400,
            detail="This video platform is not supported.",
        )

    return url


@app.get("/")
def home():
    return {
        "service": "FYTI Downloader API",
        "status": "running",
        "version": "1.1.0",
        "docs": "/docs",
        "health": "/health",
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
        "socket_timeout": 20,
        "retries": 2,
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
    except Exception as exc:
        logger.exception("FYTI video info failed: %s", exc)
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not read this video. Check Render logs "
                "for the actual error."
            ),
        )


@app.post("/download")
def download_video(request: VideoRequest):
    url = validate_url(request.url)
    quality = request.quality.lower().strip()

    if quality not in QUALITY_MAP:
        raise HTTPException(
            status_code=400,
            detail="Choose a supported quality.",
        )

    temp_dir = tempfile.mkdtemp(prefix="fyti_")
    folder = Path(temp_dir)

    options = {
        "format": QUALITY_MAP[quality],
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared_name = ydl.prepare_filename(info)

        prepared = Path(prepared_name)

        # Some formats may use a different final extension.
        if not prepared.exists():
            alternatives = list(folder.glob(prepared.stem + ".*"))
            alternatives = [
                p for p in alternatives
                if p.is_file()
                and not p.name.endswith((".part", ".ytdl"))
            ]
            if alternatives:
                prepared = alternatives[0]

        if not prepared.exists() or prepared.stat().st_size == 0:
            raise RuntimeError("Downloaded file was not found.")

        if prepared.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("File exceeds the 100 MB limit.")

        safe_name = re.sub(
            r"[^A-Za-z0-9._-]",
            "_",
            prepared.name,
        )

        return FileResponse(
            path=str(prepared),
            filename=safe_name,
            media_type="application/octet-stream",
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True,
            ),
        )

    except Exception as exc:
        logger.exception("FYTI download failed: %s", exc)
        shutil.rmtree(temp_dir, ignore_errors=True)

        raise HTTPException(
            status_code=422,
            detail=(
                "Download failed. Check Render logs for the "
                "actual error. The video may be restricted, "
                "unsupported, or exceed the server limit."
            ),
        )
