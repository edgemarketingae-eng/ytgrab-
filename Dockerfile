FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8000
EXPOSE 8000
# yt-dlp is upgraded at start so YouTube changes don't break the site
CMD pip install -U --no-cache-dir yt-dlp >/dev/null 2>&1; \
    uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers
