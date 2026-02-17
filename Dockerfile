# ── Build stage ───────────────────────────────────────────
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Build deps for native extensions (lxml → libxml2/libxslt)
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install .

# ── Runtime stage ─────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime-only native libs (no headers / compilers)
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /workspace

ENTRYPOINT ["pichu"]
