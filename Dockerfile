FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    nodejs \
    npm \
    chromium \
    fonts-liberation \
    libgbm1 \
    libnss3 \
    libxss1 \
    && rm -rf /var/lib/apt/lists/*

# Puppeteer: use system Chromium, skip bundled binary download
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_SKIP_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
# --no-sandbox is safe inside a Docker container (the container IS the sandbox)
# PUPPETEER_LAUNCH_OPTIONS is read by @modelcontextprotocol/server-puppeteer
ENV PUPPETEER_LAUNCH_OPTIONS='{"headless":true,"args":["--no-sandbox","--disable-setuid-sandbox"]}'
# ALLOW_DANGEROUS lets server-puppeteer accept --no-sandbox without throwing
ENV ALLOW_DANGEROUS=true

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir "setuptools>=69"
RUN pip install --no-cache-dir --no-build-isolation .

# Pre-install MCP server packages to avoid npx cold-start timeouts.
# Placed before COPY so this layer is only invalidated when the Dockerfile changes,
# not on every source code change.
RUN npm install -g @modelcontextprotocol/server-puppeteer \
    @modelcontextprotocol/server-filesystem \
    @modelcontextprotocol/server-github \
    @modelcontextprotocol/server-memory \
    mcp-fetch-server

COPY app/ app/
COPY skills/ skills/

ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appuser && useradd -u $UID -g $GID -m appuser \
    && mkdir -p /app/data /home/appuser/.cache/huggingface /home/appuser/.npm \
    && chown -R appuser:appuser /app /home/appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

LABEL org.opencontainers.image.source="https://github.com/sebadp/wasap"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
