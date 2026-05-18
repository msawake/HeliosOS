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
RUN pip install --no-cache-dir --prefix=/install ".[production,scheduler]" docker kubernetes \
    || pip install --no-cache-dir --prefix=/install \
        "pyyaml>=6.0" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.34" \
        "anthropic>=0.52" \
        "openai>=1.0" \
        "python-dotenv>=1.0" \
        "psycopg[binary,pool]>=3.2" \
        "redis>=5.0" \
        "httpx>=0.27" \
        "mcp>=1.0" \
        "apscheduler>=3.10" \
        "docker>=7.0" \
        "kubernetes>=28.0"

# ---------------------------------------------------------------------------
# Stage 2 — api (runtime)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS api

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 forgeos \
    && useradd -u 1000 -g forgeos -s /sbin/nologin -m -d /home/forgeos forgeos

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (ownership matches the non-root user)
COPY --chown=forgeos:forgeos src/ src/
COPY --chown=forgeos:forgeos stacks/ stacks/
COPY --chown=forgeos:forgeos config/ config/
COPY --chown=forgeos:forgeos infrastructure/database/ infrastructure/database/
COPY --chown=forgeos:forgeos resources/ resources/
COPY --chown=forgeos:forgeos scripts/ scripts/
COPY --chown=forgeos:forgeos files/ files/
COPY --chown=forgeos:forgeos pyproject.toml .

# Create writable runtime dirs. K8s mounts PVCs at these paths; local `docker run`
# uses these directly. fsGroup 1000 on K8s matches the user below.
RUN mkdir -p /app/agents /app/files/knowledge /home/forgeos/.openclaw-forgeos \
    && chown -R forgeos:forgeos /app/agents /app/files /home/forgeos/.openclaw-forgeos

USER forgeos

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=5000

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# tini ensures proper signal handling and zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: boot platform with dashboard + main loop
CMD ["python", "-m", "src.bootstrap", "--dashboard", "--loop"]
