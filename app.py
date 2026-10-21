
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp

app = FastAPI(title="FYTI Downloader API")

# Replace with your actual GitHub Pages origin before deployment.
ALLOWED_ORIGIN = os.getenv(
    "ALLOWED_ORIGIN",
    "https://saifxpayment-ux.github.io"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "facebook.com", "www.facebook.com", "fb.watch",
    "instagram.com", "www.instagram.com",
}

QUALITY_HEIGHTS = {
    "460p": 460,
    "720p": 720,
    "1080p": 1080,
    "2k": 1440,
    "4k": 2160,
}

class DownloadRequest(BaseModel):
    url: HttpUrl
    quality: str = "720p"

def check_url(value):
    parsed = urlparse(str(value))
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise HTTPException(400, "HTTPS links only.")

    if not any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_HOSTS
    ):
        raise HTTPException(400, "Unsupported platform.")

    return str(value)

@app.get("/")
def home():
    return {
        "service": "FYTI Downloader API",
        "status": "online",
        "message": "API is running."
    }

@app.post("/download")
def download_video(req: DownloadRequest):
    url = check_url(req.url)

    if req.quality not in QUALITY_HEIGHTS:
        raise HTTPException(400, "Unsupported quality.")

    # This demo supports publicly accessible videos only.
    # Never use it to bypass login, DRM, or access restrictions.
    max_height = QUALITY_HEIGHTS[req.quality]

    with tempfile.TemporaryDirectory() as temp_dir:
        output_template = str(Path(temp_dir) / "video.%(ext)s")

        options = {
            "format": (
                f"best[height<={max_height}][ext=mp4]/"
                f"best[height<={max_height}]/best"
            ),
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "max_filesize": 100 * 1024 * 1024,
            "retries": 1,
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)

            # Return the file while the temporary directory exists.
            if not os.path.isfile(filename):
                candidates = list(Path(temp_dir).glob("video.*"))
                if not candidates:
                    raise HTTPException(502, "Video file unavailable.")
                filename = str(candidates[0])

            return FileResponse(
                filename,
                media_type="application/octet-stream",
                filename="FYTI-video" + Path(filename).suffix,
                background=None,
            )

        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                502,
                "Download failed. This video may be unsupported, "
                "restricted, or unavailable."
            )
