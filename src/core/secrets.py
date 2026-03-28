"""
Secrets management for ForgeOS SaaS.

Fetches API keys and credentials from GCP Secret Manager in production,
falls back to environment variables in development.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from google.cloud import secretmanager
    HAS_SECRET_MANAGER = True
except ImportError:
    HAS_SECRET_MANAGER = False


class SecretsManager:
    """
    Retrieves secrets from GCP Secret Manager with caching.
    Falls back to environment variables when not in GCP.
    """

    def __init__(self, project_id: str | None = None, cache_ttl: int = 300):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._cache: dict[str, tuple[str, float]] = {}  # name -> (value, expires_at)
        self._cache_ttl = cache_ttl
        self._client = None

        if HAS_SECRET_MANAGER and self._project_id:
            try:
                self._client = secretmanager.SecretManagerServiceClient()
                logger.info("Secret Manager initialized (project: %s)", self._project_id)
            except Exception as e:
                logger.warning("Secret Manager unavailable: %s", e)

    def get(self, name: str, default: str = "") -> str:
        """Get a secret value. Checks cache → Secret Manager → env vars."""
        # Check cache
        if name in self._cache:
            value, expires_at = self._cache[name]
            if time.time() < expires_at:
                return value

        # Try Secret Manager
        if self._client and self._project_id:
            try:
                secret_name = f"projects/{self._project_id}/secrets/{name}/versions/latest"
                response = self._client.access_secret_version(name=secret_name)
                value = response.payload.data.decode("UTF-8")
                self._cache[name] = (value, time.time() + self._cache_ttl)
                return value
            except Exception as e:
                logger.debug("Secret '%s' not in Secret Manager: %s", name, e)

        # Fall back to environment variable
        env_name = name.upper().replace("-", "_")
        return os.environ.get(env_name, default)

    def get_tenant_secret(self, tenant_id: str, key: str, default: str = "") -> str:
        """Get a tenant-specific secret (e.g., their Anthropic API key)."""
        return self.get(f"tenant-{tenant_id}-{key}", default)

    def get_anthropic_key(self, tenant_id: str | None = None) -> str:
        """Get the Anthropic API key for a tenant or the platform default."""
        if tenant_id:
            key = self.get_tenant_secret(tenant_id, "anthropic-key")
            if key:
                return key
        return self.get("anthropic-api-key", os.environ.get("ANTHROPIC_API_KEY", ""))

    def get_openai_key(self, tenant_id: str | None = None) -> str:
        """Get the OpenAI API key for a tenant or the platform default."""
        if tenant_id:
            key = self.get_tenant_secret(tenant_id, "openai-key")
            if key:
                return key
        return self.get("openai-api-key", os.environ.get("OPENAI_API_KEY", ""))

    def get_stripe_key(self) -> str:
        """Get the Stripe API key."""
        return self.get("stripe-api-key", os.environ.get("STRIPE_API_KEY", ""))

    def get_database_url(self) -> str:
        """Get the database URL."""
        return self.get("database-url", os.environ.get("DATABASE_URL", ""))
