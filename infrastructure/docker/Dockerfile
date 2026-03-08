FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[production]" 2>/dev/null || pip install --no-cache-dir pyyaml flask

# Copy application code
COPY src/ src/
COPY config/ config/
COPY .claude/ .claude/

# Environment
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Default: boot company with dashboard
CMD ["python", "-m", "src.bootstrap", "--dashboard", "--loop"]
