# ---- Stage 1: build the free PO-token provider (helps pass YouTube bot checks) ----
FROM node:22-bookworm-slim AS pot
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN git clone --depth 1 --single-branch --branch 2.0.2 \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    && cd bgutil-ytdlp-pot-provider/server && npm ci && npx tsc

# ---- Stage 2: the app ----
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Node.js runtime: used by yt-dlp to solve YouTube JS challenges + runs the PO-token server
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=pot /opt/bgutil-ytdlp-pot-provider/server /opt/pot-server

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8000
EXPOSE 8000
CMD node /opt/pot-server/build/main.js >/tmp/pot.log 2>&1 & \
    pip install -U --no-cache-dir "yt-dlp[default]" bgutil-ytdlp-pot-provider >/dev/null 2>&1; \
    uvicorn app:app --host 0.0.0.0 --port ${PORT} --proxy-headers
