# YTGrab — YouTube to MP3 & MP4

Self-hosted web app that converts YouTube videos to **MP4 (360p–1080p)** or **MP3 (128–320 kbps, with cover art)**.
Built with FastAPI, yt-dlp and FFmpeg. One Docker container, no database.

## Features
- Paste link → preview (thumbnail, title, channel, duration)
- MP4 up to 1080p with merged audio, MP3 up to 320 kbps with ID3 tags + thumbnail
- Live progress bar (download → FFmpeg conversion)
- Per-IP rate limiting, max-duration cap, YouTube-only URL validation
- Files auto-deleted after 30 minutes
- GitHub Actions: tests on every push, Docker image published to GHCR, weekly rebuild for fresh yt-dlp

## Run locally
```bash
# Docker (recommended)
docker compose up --build
# → http://localhost:8000

# or plain Python (needs ffmpeg installed)
pip install -r requirements.txt
uvicorn app:app --reload
```

## Push to GitHub
```bash
git init && git add . && git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/ytgrab.git
git push -u origin main
```
The CI workflow runs automatically and publishes `ghcr.io/<you>/ytgrab:latest`.

## Deploy (auto-deploys from GitHub)
GitHub Pages / Vercel / Netlify **won't work** — they can't run FFmpeg or long downloads. Use a container host:

| Host | How |
|------|-----|
| **Render** | New → Blueprint → pick the repo (uses `render.yaml`) |
| **Railway** | New Project → Deploy from GitHub repo (detects Dockerfile) |
| **Fly.io** | `fly launch` → `fly deploy` |
| **VPS** | `docker compose up -d` behind Nginx/Caddy for HTTPS |

## Configuration (env vars)
| Var | Default | Purpose |
|-----|---------|---------|
| `FILE_TTL_SECONDS` | 1800 | Delete finished files after N seconds |
| `MAX_DURATION_SECONDS` | 7200 | Reject longer videos |
| `RATE_LIMIT_PER_MIN` | 10 | Requests per IP per minute |
| `YTDLP_COOKIES` | – | Path to a `cookies.txt` if YouTube blocks your server IP ("Sign in to confirm you're not a bot") |

## API
| Method | Path | Body |
|--------|------|------|
| POST | `/api/info` | `{ "url": "..." }` |
| POST | `/api/download` | `{ "url": "...", "format": "mp3"/"mp4", "quality": "720" }` → `{ job_id }` |
| GET | `/api/progress/{job_id}` | – |
| GET | `/api/file/{job_id}` | – |

## Legal
Downloading YouTube content may breach YouTube's Terms of Service. Use only for videos you own, have permission for, or that are licensed for reuse (e.g. Creative Commons). Don't run this as a public service without legal advice.
