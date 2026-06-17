"""
Secrets management for Helios OS SaaS.

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
        db_backend: Any = None,
    ):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._cache: dict[str, tuple[str, float]] = {}  # name -> (value, expires_at)
        self._cache_ttl = cache_ttl
        self._client = None
        # Phase 3 #4 — optional audit sink. Any object with ``record(action, ...)``.
        self._audit_recorder = audit_recorder
        # Optional encrypted Postgres backend (see src/core/secret_backends.py).
        # Consulted between the cache and GCP Secret Manager, and used as the
        # write target when Secret Manager is unavailable (local dev + per-user
        # credentials). Lets `secret:<name>` MCP refs resolve without GCP.
        self._db_backend = db_backend

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
        allow_env: bool = True,
    ) -> str:
        """Get a secret value. Checks cache → Secret Manager → env vars.

        Every access is logged when an ``audit_recorder`` is wired (the
        detail payload never contains the value itself — just name,
        source, and caller).

        ``allow_env=False`` disables the environment-variable fallback so a
        miss returns ``default`` immediately. Scoped resolution walks
        (CredentialStore.resolve) use this while probing candidate names, so a
        coincidentally-matching env var can't shadow a more-specific scope.
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

        # Try the encrypted Postgres backend (local dev + per-user creds).
        if self._db_backend is not None:
            try:
                val = self._db_backend.get(name)
            except Exception:
                logger.debug("Secret backend get failed for '%s'", name, exc_info=True)
                val = None
            if val is not None:
                self._cache[name] = (val, time.time() + self._cache_ttl)
                self._emit_audit(
                    "secret.read", name=name, source="postgres",
                    caller=caller, reason=reason,
                )
                return val

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

        # Fall back to environment variable (unless disabled for scope probing)
        if not allow_env:
            self._emit_audit(
                "secret.read", name=name, source="absent", caller=caller, reason=reason,
            )
            return default
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
        user_id: str = "default",
        kind: str = "generic",
        scope: str = "user",
        namespace: str | None = None,
    ) -> bool:
        """Write or overwrite a secret.

        Prefers GCP Secret Manager (creates the resource on first call, always
        adds a new version). When Secret Manager is unavailable, falls back to
        the encrypted Postgres backend if one is wired (local dev + per-user
        credentials). ``user_id``/``kind``/``scope``/``namespace`` are recorded
        by the Postgres backend for lookup; they are ignored by Secret Manager
        (where the scope is already encoded in ``name``). Returns True on
        success, False if no writable backend exists (env-var fallback is
        intentionally write-disabled — secrets must land in a real store).
        """
        if not (self._client and self._project_id):
            if self._db_backend is not None:
                ok = self._db_backend.put(
                    name, value, user_id=user_id, kind=kind, scope=scope, namespace=namespace,
                )
                if ok:
                    self._cache.pop(name, None)
                    self._emit_audit(
                        "secret.write", name=name, source="postgres",
                        caller=caller, reason=reason,
                    )
                return ok
            logger.warning("No writable secret backend; refusing to put '%s'", name)
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

    def delete(self, name: str, *, caller: str = "", reason: str = "") -> bool:
        """Delete a secret from every writable backend. Idempotent."""
        self._cache.pop(name, None)
        ok = False
        if self._db_backend is not None and hasattr(self._db_backend, "delete"):
            try:
                ok = bool(self._db_backend.delete(name))
            except Exception:
                logger.debug("Secret backend delete failed for '%s'", name, exc_info=True)
        if self._client and self._project_id:
            try:
                self._client.delete_secret(
                    request={"name": f"projects/{self._project_id}/secrets/{name}"}
                )
                ok = True
            except Exception as e:
                logger.debug("Secret '%s' not deleted from Secret Manager: %s", name, e)
        self._emit_audit("secret.delete", name=name, caller=caller, reason=reason)
        return ok

    def list_names(
        self,
        *,
        scope: str = "user",
        namespace: str | None = None,
        user_id: str | None = None,
        gcp_prefix: str | None = None,
    ) -> list[dict]:
        """List stored secrets at a tier — names + metadata only, never values.

        Merges results from BOTH backends: the encrypted Postgres column query
        AND a GCP Secret Manager prefix scan (``gcp_prefix`` supplied by the
        caller, which owns the scope→name convention). Both are consulted
        because writes prefer GCP when a project is configured while legacy
        rows may still live in Postgres. Returns ``[]`` when neither enumerates.
        """
        out: dict[str, dict] = {}
        if self._db_backend is not None and hasattr(self._db_backend, "list_names"):
            try:
                for r in self._db_backend.list_names(scope=scope, namespace=namespace, user_id=user_id):
                    name = r.get("secret_name")
                    if name:
                        out[name] = r
            except Exception:
                logger.debug("Secret backend list_names failed (scope=%s)", scope, exc_info=True)
        if self._client and self._project_id and gcp_prefix:
            try:
                parent = f"projects/{self._project_id}"
                for s in self._client.list_secrets(request={"parent": parent}):
                    sid = s.name.rsplit("/", 1)[-1]
                    if sid.startswith(gcp_prefix) and sid not in out:
                        out[sid] = {"secret_name": sid, "kind": "generic",
                                    "scope": scope, "namespace": namespace, "user_id": user_id}
            except Exception:
                logger.debug("Secret Manager list_secrets failed", exc_info=True)
        return list(out.values())
