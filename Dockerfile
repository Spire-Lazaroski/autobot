# autobot in one container: the lab (API + built web pages, daily report, nightly recalibration), the news
# collector and the paper bot. Saved state (config.yaml, data/, logs/, reports/) lives in STATE_DIR, which must be
# a permanent volume. See docker/README.md. Paper trading only: live.py hard-codes paper=True.

# ---- 1. the lab's web pages
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. the app
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PORT=8080 STATE_DIR=/state AUTOSTART_BOT=1
# chromium: the PDF copy of the daily report (without it the report is HTML only)
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY . .
COPY --from=web /web/dist ./web/dist
RUN chmod +x docker/entrypoint.sh
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"
ENTRYPOINT ["docker/entrypoint.sh"]
