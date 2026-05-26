"""
Secrets management for ForgeOS SaaS.

Fetches API keys and credentials from GCP Secret Manager in production,
falls back to environment variables in development.

Phase 3 #4 — short-TTL leases + access audit.

Every ``get()`` now accepts a ``reason`` and ``caller`` for audit
recording. An injected ``audit_recorder`` (any object with ``record(action,
...)``) receives one row per fetch — cache hit included — so tamper
analysis and anomaly detection have a full access trail. The cache TTL
(``cache_ttl``) bounds how long a secret can sit in memory before it's
re-fetched, giving us revocation propagation on the same order as
``cache_ttl``. A default of 60s is reasonable for agent runtimes; the
old 300s default is kept here for compatibility but callers should pass
a smaller value.
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

    def __init__(
        self,
        project_id: str | None = None,
        cache_ttl: int = 300,
        audit_recorder: Any = None,
    ):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._cache: dict[str, tuple[str, float]] = {}  # name -> (value, expires_at)
        self._cache_ttl = cache_ttl
        self._client = None
        # Phase 3 #4 — optional audit sink. Any object with ``record(action, ...)``.
        self._audit_recorder = audit_recorder

        if HAS_SECRET_MANAGER and self._project_id:
            try:
                self._client = secretmanager.SecretManagerServiceClient()
                logger.info("Secret Manager initialized (project: %s)", self._project_id)
            except Exception as e:
                logger.warning("Secret Manager unavailable: %s", e)

    # -- lease / cache management -----------------------------------------

    def invalidate(self, name: str) -> bool:
        """Drop a secret from the cache, forcing a re-fetch on next get().

        Returns True if the entry existed. Useful for immediate revocation
        without waiting for the TTL to elapse.
        """
        existed = self._cache.pop(name, None) is not None
        if existed:
            self._emit_audit("secret.invalidate", name=name)
        return existed

    def invalidate_all(self) -> int:
        """Drop every cached lease. Returns the count invalidated."""
        n = len(self._cache)
        self._cache.clear()
        if n:
            self._emit_audit("secret.invalidate_all", count=n)
        return n

    def lease_remaining(self, name: str) -> float | None:
        """Return seconds of TTL left for ``name``, or ``None`` if not cached."""
        entry = self._cache.get(name)
        if entry is None:
            return None
        _, expires_at = entry
        return max(0.0, expires_at - time.time())

    # -- access --------------------------------------------------------------

    def _emit_audit(self, action: str, **details: Any) -> None:
        recorder = self._audit_recorder
        if recorder is None or not hasattr(recorder, "record"):
            return
        try:
            recorder.record(
                action=action,
                resource_type="secret",
                resource_id=details.get("name", ""),
                details=details,
            )
        except Exception:
            logger.warning("secret audit recorder failed for action=%s", action, exc_info=True)

    def get(
        self,
        name: str,
        default: str = "",
        *,
        caller: str = "",
        reason: str = "",
    ) -> str:
        """Get a secret value. Checks cache → Secret Manager → env vars.

        Every access is logged when an ``audit_recorder`` is wired (the
        detail payload never contains the value itself — just name,
        source, and caller).
        """
        # Check cache
        if name in self._cache:
            value, expires_at = self._cache[name]
            if time.time() < expires_at:
                self._emit_audit(
                    "secret.read",
                    name=name,
                    source="cache",
                    caller=caller,
                    reason=reason,
                )
                return value
            # Expired: purge so the miss path re-fetches.
            del self._cache[name]
            self._emit_audit("secret.lease_expired", name=name, caller=caller)

        # Try Secret Manager
        if self._client and self._project_id:
            try:
                secret_name = f"projects/{self._project_id}/secrets/{name}/versions/latest"
                response = self._client.access_secret_version(name=secret_name)
                value = response.payload.data.decode("UTF-8")
                self._cache[name] = (value, time.time() + self._cache_ttl)
                self._emit_audit(
                    "secret.read",
                    name=name,
                    source="secret_manager",
                    caller=caller,
                    reason=reason,
                )
                return value
            except Exception as e:
                logger.debug("Secret '%s' not in Secret Manager: %s", name, e)

        # Fall back to environment variable
        env_name = name.upper().replace("-", "_")
        value = os.environ.get(env_name, default)
        self._emit_audit(
            "secret.read",
            name=name,
            source="env" if env_name in os.environ else "default",
            caller=caller,
            reason=reason,
        )
        return value

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

    def put(
        self,
        name: str,
        value: str,
        *,
        caller: str = "",
        reason: str = "",
    ) -> bool:
        """Write or overwrite a Secret Manager secret.

        Creates the secret resource on first call and always adds a new
        version. Returns True on success, False if Secret Manager is not
        available (env-var fallback is intentionally write-disabled — secrets
        must land in the configured secret store, not the process env).
        """
        if not (self._client and self._project_id):
            logger.warning("Secret Manager unavailable; refusing to put '%s'", name)
            return False
        parent = f"projects/{self._project_id}"
        try:
            try:
                self._client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": name,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except Exception as e:
                # AlreadyExists is fine — we just want to add a new version.
                if "already exists" not in str(e).lower() and "alreadyexists" not in type(e).__name__.lower():
                    raise
            self._client.add_secret_version(
                request={
                    "parent": f"{parent}/secrets/{name}",
                    "payload": {"data": value.encode("utf-8")},
                }
            )
        except Exception:
            logger.exception("Failed to write secret '%s'", name)
            return False
        # Drop any cached value so callers see the new version immediately.
        self._cache.pop(name, None)
        self._emit_audit("secret.write", name=name, caller=caller, reason=reason)
        return True
