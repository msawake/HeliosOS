# ForgeOS — portable OCI image (multi-target).
# Default image (last stage): API-only for Kubernetes / Ingress.
#   docker build -f infrastructure/docker/Dockerfile -t forgeos-api:latest .
# Monolith (API + background loop):
#   docker build --target monolith -f infrastructure/docker/Dockerfile -t forgeos:monolith .

FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir ".[production]" 2>/dev/null || pip install --no-cache-dir pyyaml flask anthropic

COPY src/ src/
COPY stacks/ stacks/
COPY config/ config/
COPY infrastructure/database/schema.sql infrastructure/database/schema.sql

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

EXPOSE ${PORT}

FROM base AS monolith
CMD ["python", "-m", "src.bootstrap", "--dashboard", "--loop"]

# Default: HTTP API only — pair with dashboard image behind Ingress.
FROM base AS api
CMD ["python", "-m", "src.bootstrap", "--dashboard"]
