# syntax=docker/dockerfile:1.6
# =============================================================================
# Helios OS API image
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
RUN pip install --no-cache-dir --prefix=/install ".[production,scheduler,django]" \
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
        "requests>=2.31" \
        "django>=5.2,<6.0" \
        "djangorestframework>=3.15" \
        "django-cors-headers>=4.4" \
        "dj-database-url>=2.2" \
        "pgvector>=0.3" \
        "celery[redis]>=5.4" \
        "django-celery-beat>=2.7"

# Belt-and-braces: ensure the GCP libs the platform reaches for at runtime
# (Secret Manager for per-user PATs, Storage / Logging for ops) are present
# even when the fallback above kicked in because pip couldn't build the
# project from this stripped-down COPY context.
RUN pip install --no-cache-dir --prefix=/install \
    "google-cloud-secret-manager>=2.16" \
    "google-cloud-logging>=3.5" \
    "google-cloud-storage>=2.10"

# Optional commercial plugin (`heliosos` / `forgeos_billing`). The Cloud Build
# release/dev pipelines build the wheel from the private heliosos-enterprise repo
# (using a CI-only PAT from Secret Manager) and drop it in vendor-wheels/ before
# the docker build; the private source never lands in this public repo. When no
# wheel is present (open-source builds, local dev) this is a no-op and the
# platform boots in OSS mode via the existing `except ImportError` guards in
# src/bootstrap.py + forgeos_web/urls.py.
COPY vendor-wheels/ /tmp/vendor-wheels/
RUN if ls /tmp/vendor-wheels/heliosos-*.whl >/dev/null 2>&1; then \
        pip install --no-cache-dir --prefix=/install /tmp/vendor-wheels/heliosos-*.whl \
        && echo "enterprise plugin: installed $(ls /tmp/vendor-wheels/heliosos-*.whl)"; \
    else \
        echo "enterprise plugin: no wheel in build context — open-source mode"; \
    fi

# ---------------------------------------------------------------------------
# Stage 2 — api (runtime)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS api

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tini ca-certificates git gnupg unzip \
        # Playwright Chromium shared libs — required for the
        # forgeos-lens-tester to run `pnpm test:e2e` (the smoke harness
        # added in TODO #13). The forgeos user can't sudo, so we install
        # these at image build time instead of `playwright install --with-deps`.
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && groupadd -g 1000 forgeos \
    && useradd -u 1000 -g forgeos -s /bin/bash -m -d /home/forgeos forgeos \
    # Install uv (provides uvx) into /usr/local/bin so non-root user can spawn MCP servers.
    && curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh \
    # Node 22 (NodeSource). qwen-code requires Node >= 22; vite 8 + the lens
    # 2026 toolchain also need Node 22 for their esbuild integration.
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # pnpm + qwen-code — pnpm runs `pnpm install`/`pnpm build` for the
    # forgeos-lens build pipeline; qwen-code is the agent's coding CLI (driven
    # via the code__qwen_code_run dev tool). qwen-code reads OPENAI_API_KEY /
    # OPENAI_BASE_URL / OPENAI_MODEL purely from env, so no settings.json seed.
    && npm install -g pnpm @qwen-code/qwen-code@latest \
    # GitHub CLI (forgeos-lens-builder + pr-reviewer use it via shell__exec / gh__open_pr).
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    # Google Cloud SDK (gcloud) — sre-gcp-auditor runs read-only `gcloud ...
    # list/describe --format=json` via shell__exec. On Cloud Run gcloud
    # authenticates through the metadata server (ADC) using the service account.
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (ownership matches the non-root user)
COPY --chown=forgeos:forgeos src/ src/
COPY --chown=forgeos:forgeos stacks/ stacks/
COPY --chown=forgeos:forgeos a2h/ a2h/
# The Django web layer + management entrypoint live at the repo root since the
# restructure (bfc7ad09). Required by `--dashboard` (Django ASGI), the Celery
# worker/beat (`celery -A forgeos_web.celery_app`), and `manage.py migrate`.
COPY --chown=forgeos:forgeos forgeos_web/ forgeos_web/
COPY --chown=forgeos:forgeos manage.py .
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
 && git config --global init.defaultBranch main \
 # Credential helper so a plain `git push` over https authenticates with the
 # per-invocation GH_TOKEN that shell__exec injects (not just the dedicated
 # git__commit_push tool, which uses http.extraHeader). x-access-token is the
 # GitHub convention for token-as-password.
 && git config --global credential.helper '!f() { echo username=x-access-token; echo "password=${GH_TOKEN}"; }; f'

# Warm the uv/uvx cache for popular stdio MCPs so the FIRST agent invocation
# after a worker pod restart doesn't burn its 45s MCP-initialize timeout on a
# cold `uvx <pkg>` install (~115 wheels for mcp-atlassian alone). Anything
# listed here is a persistent `uv tool install` under
# ~/.local/share/uv/tools/<pkg>; `uvx <pkg>` finds it instantly. Add entries as
# more common MCPs land; keep the list small so the image stays tight.
ENV UV_CACHE_DIR=/home/forgeos/.cache/uv
RUN uv tool install mcp-atlassian || echo "warn: uv tool install mcp-atlassian failed, cold-start will fall back to ephemeral uvx"

ENV PYTHONPATH=/app:/app/a2h \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    # qwen-code defaults — agents can override per-call via the tool's
    # `model` / `base_url` parameters; Cloud Run env can override at deploy time.
    FORGEOS_QWEN_BASE_URL=http://79.117.23.16:20327/v1 \
    FORGEOS_QWEN_MODEL=qwen3.6-27b

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# tini ensures proper signal handling and zombie reaping
ENTRYPOINT ["/usr/bin/tini", "--"]

# Default: boot platform with dashboard + main loop
CMD ["python", "-m", "src.bootstrap", "--dashboard", "--loop"]
