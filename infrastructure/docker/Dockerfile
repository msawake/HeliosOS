# syntax=docker/dockerfile:1.6
# =============================================================================
# ForgeOS API image
#
# Two-stage build:
#   builder → installs production deps into /install (cached by pyproject.toml)
#   api     → slim runtime that copies /install + src/, runs as non-root user
#
# Build:
#   docker buildx build --target api -f infrastructure/docker/Dockerfile -t forgeos-api:test .
#
# CI note: `.github/workflows/docker-publish.yml` passes `target: api` — do not
# rename that stage without updating the workflow.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

# Install into a prefix we'll copy into the runtime image.
# `[production,scheduler]` pulls openai + mcp + apscheduler + psycopg[binary,pool]
# + redis + GCP libs + stripe + otel. Fallback keeps a minimal install for
# environments that can't read the optional extras.
RUN pip install --no-cache-dir --prefix=/install ".[production,scheduler,server]" \
    || pip install --no-cache-dir --prefix=/install \
        "pyyaml>=6.0" \
        "starlette>=0.36" \
        "uvicorn[standard]>=0.30" \
        "anthropic>=0.52" \
        "openai>=1.0" \
        "python-dotenv>=1.0" \
        "psycopg[binary,pool]>=3.2" \
        "redis>=5.0" \
        "httpx>=0.27" \
        "mcp>=1.0" \
        "apscheduler>=3.10" \
        "google-auth>=2.0" \
        "requests>=2.31"

# ---------------------------------------------------------------------------
# Stage 2 — api (runtime)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS api

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 forgeos \
    && useradd -u 1000 -g forgeos -s /sbin/nologin -m -d /home/forgeos forgeos \
    # Install uv (provides uvx) into /usr/local/bin so non-root user can spawn MCP servers.
    && curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (ownership matches the non-root user)
COPY --chown=forgeos:forgeos src/ src/
COPY --chown=forgeos:forgeos stacks/ stacks/
COPY --chown=forgeos:forgeos a2h/ a2h/
COPY --chown=forgeos:forgeos config/ config/
COPY --chown=forgeos:forgeos infrastructure/database/ infrastructure/database/
COPY --chown=forgeos:forgeos resources/ resources/
COPY --chown=forgeos:forgeos pyproject.toml .

# Create writable runtime dirs. K8s mounts PVCs at these paths; local `docker run`
# uses these directly. fsGroup 1000 on K8s matches the user below.
RUN mkdir -p /app/agents /home/forgeos/.openclaw-forgeos \
    && chown -R forgeos:forgeos /app/agents /home/forgeos/.openclaw-forgeos

USER forgeos

ENV PYTHONPATH=/app:/app/a2h \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    UV_CACHE_DIR=/home/forgeos/.cache/uv

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# tini ensures proper signal handling and zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: run the thin-client HTTP server (Starlette) that the Rust
# `forgeos` CLI talks to. Listens on $PORT (default 5000).
CMD ["sh", "-c", "exec python -m src.forgeos_sdk.local_server --host 0.0.0.0 --port ${PORT:-5000}"]
