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
RUN pip install --no-cache-dir --prefix=/install ".[production,scheduler]" \
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
        "google-auth>=2.0" \
        "requests>=2.31"

# ---------------------------------------------------------------------------
# Stage 2 — api (runtime)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS api

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tini ca-certificates git gnupg unzip \
    && groupadd -g 1000 forgeos \
    && useradd -u 1000 -g forgeos -s /bin/bash -m -d /home/forgeos forgeos \
    # Install uv (provides uvx) into /usr/local/bin so non-root user can spawn MCP servers.
    && curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh \
    # Node 20 for opencode (NodeSource).
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # GitHub CLI (forgeos-lens-builder + pr-reviewer use it via shell__exec / gh__open_pr).
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    # opencode CLI — installs to /root/.opencode/bin; move to /usr/local/bin so the
    # non-root forgeos user can find it on $PATH.
    && curl -fsSL https://opencode.ai/install | bash \
    && mv /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && rm -rf /var/lib/apt/lists/* /root/.opencode

# Seed opencode provider config so the atlas/nvidia/nemotron-3-super model
# routes to the rtx3 vLLM endpoint with zero per-call configuration. The
# inner `opencode models` call also pre-warms the @ai-sdk/openai-compatible
# npm dep into /home/forgeos/.config/opencode/node_modules, so first-call
# latency in production stays low.
RUN mkdir -p /home/forgeos/.config/opencode \
 && printf '%s\n' \
    '{' \
    '  "$schema": "https://opencode.ai/config.json",' \
    '  "model": "atlas/nvidia/nemotron-3-super",' \
    '  "provider": {' \
    '    "atlas": {' \
    '      "npm": "@ai-sdk/openai-compatible",' \
    '      "name": "Atlas Gateway",' \
    '      "options": {"baseURL": "http://34.78.73.30:8080/v1", "apiKey": "sk-local-test"},' \
    '      "models": {' \
    '        "nvidia/nemotron-3-super": {' \
    '          "name": "Nemotron 3 Super 120B",' \
    '          "limit": {"context": 131072, "output": 16384},' \
    '          "interleaved": {"field": "reasoning_content"}' \
    '        }' \
    '      }' \
    '    }' \
    '  }' \
    '}' \
    > /home/forgeos/.config/opencode/config.json \
 && chown -R forgeos:forgeos /home/forgeos/.config

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
# /tmp/forgeos-lens-builder is the default working tree for the
# forgeos-lens-builder / pr-reviewer agents.
RUN mkdir -p /app/agents /home/forgeos/.openclaw-forgeos /tmp/forgeos-lens-builder \
    && chown -R forgeos:forgeos /app/agents /home/forgeos/.openclaw-forgeos /tmp/forgeos-lens-builder

USER forgeos

# Configure git for the forgeos user so commits from agent tools land with
# coherent author metadata (PAT auth still rides via http.extraHeader at push
# time — see src/platform/dev_tools.py).
RUN git config --global user.name "forgeos-agent" \
 && git config --global user.email "agent@forgeos.local" \
 && git config --global init.defaultBranch main

ENV PYTHONPATH=/app:/app/a2h \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    UV_CACHE_DIR=/home/forgeos/.cache/uv

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# tini ensures proper signal handling and zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: boot platform with dashboard + main loop
CMD ["python", "-m", "src.bootstrap", "--dashboard", "--loop"]
