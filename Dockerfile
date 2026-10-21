FROM python:3.12-slim AS builder

# TgCrypto needs a compiler on Python 3.12; keep build tools out of runtime.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim

LABEL description="Social Downloader Bot - Auto-download TikTok, Instagram, YouTube Shorts"

WORKDIR /app

# Install system deps needed by yt-dlp
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# YouTube challenge solving needs a JS runtime in addition to yt-dlp-ejs.
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN deno --version

# Copy bot code
COPY bot/ bot/
COPY main.py .

CMD ["python", "main.py"]
