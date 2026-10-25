"""
YTGrab — self-hosted YouTube to MP3 / MP4 downloader.
Backend: FastAPI + yt-dlp + FFmpeg.
"""
import os
import re
import time
import uuid
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp

BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

FILE_TTL_SECONDS = int(os.getenv("FILE_TTL_SECONDS", "1800"))     # delete files after 30 min
MAX_DURATION_SECONDS = int(os.getenv("MAX_DURATION_SECONDS", "7200"))  # 2h cap
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))
COOKIES_FILE = os.getenv("YTDLP_COOKIES")  # optional path to cookies.txt
PROXY = os.getenv("YTDLP_PROXY")            # optional http(s)/socks proxy URL

# Cookies can also be pasted into an env var (raw Netscape cookies.txt text
# or base64 of it). Written to a writable temp file because yt-dlp updates it.
_cookie_text = os.getenv("YTDLP_COOKIES_CONTENT", "").strip()
if _cookie_text:
    import base64
    if not _cookie_text.startswith("#") and "\t" not in _cookie_text:
        try:
            _cookie_text = base64.b64decode(_cookie_text).decode()
        except Exception:  # noqa: BLE001
            pass
    COOKIES_FILE = "/tmp/yt_cookies.txt"
    Path(COOKIES_FILE).write_text(_cookie_text.replace("\\n", "\n") + "\n")

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                 "music.youtube.com", "youtu.be"}
MP4_QUALITIES = {"1080": 1080, "720": 720, "480": 480, "360": 360}
MP3_BITRATES = {"320", "256", "192", "128"}

app = FastAPI(title="YTGrab", version="1.0.0")
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
rate_bucket: dict[str, list[float]] = {}


# ---------- helpers ----------
def validate_url(url: str) -> str:
    url = url.strip()
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        host = ""
    if host.lower() not in ALLOWED_HOSTS:
        raise HTTPException(400, "Please paste a valid YouTube link.")
    return url


def rate_limit(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    now = time.time()
    hits = [t for t in rate_bucket.get(ip, []) if now - t < 60]
    if len(hits) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(429, "Too many requests — try again in a minute.")
    hits.append(now)
    rate_bucket[ip] = hits


def base_opts() -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        # alternative clients are less often bot-checked on cloud IPs
        "extractor_args": {"youtube": {"player_client": ["default", "mweb"]}},
        # Node.js solves YouTube's JS challenges; PO tokens come from the bgutil server
        "js_runtimes": {"node": {}},
        "socket_timeout": 20,
    }
    if PROXY:
        opts["proxy"] = PROXY
    if COOKIES_FILE and Path(COOKIES_FILE).exists():
        opts["cookiefile"] = COOKIES_FILE
    return opts


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]+', "", name).strip()
    return (name or "download")[:120]


def cleanup_loop():
    while True:
        now = time.time()
        with jobs_lock:
            for jid, job in list(jobs.items()):
                if now - job["created"] > FILE_TTL_SECONDS:
                    shutil.rmtree(DOWNLOAD_DIR / jid, ignore_errors=True)
                    jobs.pop(jid, None)
        time.sleep(60)


threading.Thread(target=cleanup_loop, daemon=True).start()


# ---------- models ----------
class InfoReq(BaseModel):
    url: str


class DownloadReq(BaseModel):
    url: str
    format: str = "mp4"     # "mp3" | "mp4"
    quality: str = "720"    # mp4: 1080/720/480/360  mp3: 320/256/192/128


# ---------- worker ----------
def run_download(job_id: str, req: DownloadReq):
    out_dir = DOWNLOAD_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    job = jobs[job_id]

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            if total:
                job["progress"] = round(done / total * 90, 1)  # 0-90 = download
            job["status"] = "downloading"
        elif d["status"] == "finished":
            job["status"] = "processing"
            job["progress"] = 92

    opts = base_opts() | {
        "outtmpl": str(out_dir / "%(title).120s.%(ext)s"),
        "progress_hooks": [hook],
    }

    if req.format == "mp3":
        opts |= {
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                 "preferredquality": req.quality},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "writethumbnail": True,
        }
    else:
        h = MP4_QUALITIES[req.quality]
        opts |= {
            "format": (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                       f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"),
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([req.url])
        ext = ".mp3" if req.format == "mp3" else ".mp4"
        files = [p for p in out_dir.iterdir() if p.suffix == ext]
        if not files:
            raise RuntimeError("Output file not found after processing.")
        job.update(status="done", progress=100, file=str(files[0]),
                   filename=safe_name(files[0].stem) + ext)
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("ERROR: ", "")
        job.update(status="error", error=msg[:300])


# ---------- routes ----------
@app.get("/health")
def health():
    return {"ok": True, "ffmpeg": bool(shutil.which("ffmpeg")),
            "yt_dlp": yt_dlp.version.__version__}


@app.get("/api/check")
def check(url: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw"):
    """Quick diagnostic: can this server read YouTube right now?"""
    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL(base_opts() | {"skip_download": True}) as ydl:
            data = ydl.extract_info(url, download=False)
        return {"ok": True, "title": data.get("title"),
                "formats": len(data.get("formats", []))}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:400]}


@app.post("/api/info")
def info(req: InfoReq, request: Request):
    rate_limit(request)
    url = validate_url(req.url)
    try:
        with yt_dlp.YoutubeDL(base_opts() | {"skip_download": True}) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Couldn't read that video: {str(e)[:200]}")

    heights = sorted({f.get("height") for f in data.get("formats", [])
                      if f.get("height") and f.get("vcodec") != "none"}, reverse=True)
    available = [q for q, h in MP4_QUALITIES.items()
                 if any(x >= h for x in heights)] or ["360"]
    return {
        "title": data.get("title"),
        "channel": data.get("uploader"),
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "views": data.get("view_count"),
        "mp4_qualities": available,
        "mp3_bitrates": sorted(MP3_BITRATES, key=int, reverse=True),
    }


@app.post("/api/download")
def download(req: DownloadReq, request: Request):
    rate_limit(request)
    req.url = validate_url(req.url)
    if req.format not in ("mp3", "mp4"):
        raise HTTPException(400, "Format must be mp3 or mp4.")
    if req.format == "mp4" and req.quality not in MP4_QUALITIES:
        raise HTTPException(400, "Invalid video quality.")
    if req.format == "mp3" and req.quality not in MP3_BITRATES:
        raise HTTPException(400, "Invalid audio bitrate.")

    # duration guard
    try:
        with yt_dlp.YoutubeDL(base_opts() | {"skip_download": True}) as ydl:
            meta = ydl.extract_info(req.url, download=False)
        if (meta.get("duration") or 0) > MAX_DURATION_SECONDS:
            raise HTTPException(400, "Video is too long for this server.")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Couldn't read that video: {str(e)[:200]}")

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "progress": 0, "created": time.time()}
    threading.Thread(target=run_download, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
def progress(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired.")
    return {k: job.get(k) for k in ("status", "progress", "error", "filename")}


@app.get("/api/file/{job_id}")
def get_file(job_id: str):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "File not ready or expired.")
    media = "audio/mpeg" if job["filename"].endswith(".mp3") else "video/mp4"
    return FileResponse(job["file"], media_type=media, filename=job["filename"])


@app.exception_handler(HTTPException)
async def http_err(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
