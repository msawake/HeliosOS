"""
Per-user credential storage for agent runtime tools.

Used today for GitHub PATs, but the surface is intentionally generic so
other tool integrations (Slack, Notion, …) can ride the same plumbing.

Storage: GCP Secret Manager, key format `forgeos-<kind>-pat-<user_id>`.
Cache:   the underlying `SecretsManager` caches with a short TTL so
         revocations propagate within minutes without restart.
Audit:   every write and read emits `secret.write` / `secret.read` via
         the SecretsManager audit hook (no value in the audit body).

There is intentionally **no read-side HTTP endpoint**: secrets only flow
into a running agent process via `inject_for_invocation`, never back out
to the caller. If you need to verify storage from a CLI, store + re-store.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_USER_ID = "default"
GH_PAT_KIND = "github"
JIRA_KIND = "jira"

# Three secret tiers. Stored names are scope-qualified so one flat key space
# (works on both the encrypted-Postgres and GCP Secret Manager backends) holds
# all three. See ``scoped_secret_name``. The top tier is ``tenant`` (the table
# is RLS-isolated by tenant_id, so a tenant-scoped secret is the tenant-wide
# default). ``SCOPE_PLATFORM`` is kept as a back-compat alias for one release so
# existing imports and stored ``platform/`` pins keep working.
SCOPE_TENANT = "tenant"
SCOPE_NAMESPACE = "namespace"
SCOPE_USER = "user"
SCOPE_PLATFORM = SCOPE_TENANT  # deprecated alias → "tenant"
SCOPES = (SCOPE_TENANT, SCOPE_NAMESPACE, SCOPE_USER)

# Logical secret names are kept GCP-safe (Secret Manager ids allow only
# ``[A-Za-z0-9_-]``) so the same name works on either backend.
_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")


def validate_secret_name(name: str) -> str:
    """Validate + return a logical secret name, or raise ``ValueError``."""
    name = (name or "").strip()
    if not _SECRET_NAME_RE.match(name):
        raise ValueError(
            "secret name must be 1–201 chars of [A-Za-z0-9_-] and start alphanumeric"
        )
    return name


def _canonical_scope(scope: str) -> str:
    """Map the legacy ``platform`` token to ``tenant`` (alias window)."""
    return SCOPE_TENANT if scope == "platform" else scope


def scoped_secret_name(
    name: str, *, scope: str, namespace: str | None = None, user_id: str | None = None
) -> str:
    """Construct the scope-qualified storage key for a logical secret name.

    tenant    → ``forgeos-tenant-<name>``
    namespace → ``forgeos-ns-<namespace>-<name>``
    user      → ``forgeos-user-<user_id>-<name>``
    """
    name = (name or "").strip()
    scope = _canonical_scope(scope)
    if scope == SCOPE_TENANT:
        return f"forgeos-tenant-{name}"
    if scope == SCOPE_NAMESPACE:
        return f"forgeos-ns-{namespace or DEFAULT_USER_ID}-{name}"
    if scope == SCOPE_USER:
        return f"forgeos-user-{user_id or DEFAULT_USER_ID}-{name}"
    raise ValueError(f"unknown secret scope: {scope!r}")


def scope_prefix(scope: str, *, namespace: str | None = None, user_id: str | None = None) -> str:
    """The stored-name prefix for a scope (used for listing + delogification)."""
    return scoped_secret_name("", scope=scope, namespace=namespace, user_id=user_id)


def logical_secret_name(
    stored: str, *, scope: str, namespace: str | None = None, user_id: str | None = None
) -> str:
    """Strip the scope prefix from a stored name; pass through if unprefixed
    (covers legacy ``forgeos-<kind>-<field>-<user>`` rows)."""
    p = scope_prefix(scope, namespace=namespace, user_id=user_id)
    return stored[len(p):] if stored.startswith(p) else stored


def _secret_name(kind: str, user_id: str) -> str:
    """Legacy single-field key: ``forgeos-<kind>-pat-<user_id>`` (github)."""
    user_id = user_id or DEFAULT_USER_ID
    return f"forgeos-{kind}-pat-{user_id}"


def _field_secret_name(kind: str, field: str, user_id: str) -> str:
    """Generic multi-field key: ``forgeos-<kind>-<field>-<user_id>``.

    Used for credentials with more than one part (e.g. JIRA needs url + email
    + token). The same names appear verbatim as ``secret:<name>`` references in
    per-user MCP server configs so the MCP env resolves to the stored values.
    """
    user_id = user_id or DEFAULT_USER_ID
    return f"forgeos-{kind}-{field}-{user_id}"


def jira_secret_names(user_id: str) -> dict[str, str]:
    """Return the SecretsManager keys for a user's JIRA credential fields."""
    return {
        "url": _field_secret_name(JIRA_KIND, "url", user_id),
        "email": _field_secret_name(JIRA_KIND, "email", user_id),
        "token": _field_secret_name(JIRA_KIND, "token", user_id),
    }


def allowed_scopes_for_agent(agent_def: Any) -> tuple[tuple[str, ...], str | None]:
    """Map an agent's ownership to the secret scopes it may read at runtime.

    * PERSONAL → ``(user, namespace, tenant)`` resolved for the **owner**
      (``owner_id``), not the invoker.
    * SHARED / CLIENT (namespace-owned) → ``(namespace, tenant)`` only — no user
      scope, so a shared agent can never reach a user-scoped secret.

    Returns ``(allowed_scopes, secret_user_id)`` to pass into
    :meth:`CredentialStore.resolve`. Ownership is read by value to avoid a
    circular import of ``OwnershipType`` from ``stacks.base``.
    """
    own = getattr(agent_def, "ownership", None)
    val = getattr(own, "value", own)  # OwnershipType enum or raw str
    if val == "personal":
        return (SCOPE_USER, SCOPE_NAMESPACE, SCOPE_TENANT), (getattr(agent_def, "owner_id", None) or None)
    return (SCOPE_NAMESPACE, SCOPE_TENANT), None


class CredentialStore:
    """Thin wrapper around SecretsManager for per-user developer credentials."""

    def __init__(self, secrets_manager: Any, *, tenant_id: str = DEFAULT_USER_ID) -> None:
        self._secrets = secrets_manager
        self._tenant_id = tenant_id

    def put_github_pat(self, pat: str, *, user_id: str = DEFAULT_USER_ID, caller: str = "") -> bool:
        if not pat or not pat.strip():
            raise ValueError("PAT is empty")
        return self._secrets.put(
            _secret_name(GH_PAT_KIND, user_id),
            pat.strip(),
            caller=caller,
            reason="credentials.put.github",
            user_id=user_id,
            kind=GH_PAT_KIND,
        )

    def put_secret(
        self,
        name: str,
        value: str,
        *,
        user_id: str = DEFAULT_USER_ID,
        kind: str = "generic",
        caller: str = "",
    ) -> bool:
        """Store an arbitrary named secret for a user (encrypted at rest).

        Generic write path used by per-user MCP enrollment for any server's
        secret env vars. ``name`` is the exact SecretsManager key that the MCP
        config references as ``secret:<name>``.
        """
        if value is None or not str(value).strip():
            raise ValueError(f"secret '{name}' value is empty")
        return self._secrets.put(
            name,
            str(value).strip(),
            caller=caller,
            reason=f"credentials.put.{kind}",
            user_id=user_id,
            kind=kind,
            scope=SCOPE_USER,
        )

    def put_jira(
        self,
        *,
        url: str,
        email: str,
        token: str,
        user_id: str = DEFAULT_USER_ID,
        caller: str = "",
    ) -> bool:
        """Store a user's Atlassian Cloud credential (url + email + token).

        Writes three secrets whose names match the ``secret:`` references in the
        user's MCP server config, so the JIRA MCP env resolves to these values.
        """
        fields = {"url": url, "email": email, "token": token}
        missing = [k for k, v in fields.items() if not (v and v.strip())]
        if missing:
            raise ValueError(f"jira credential missing field(s): {', '.join(missing)}")
        names = jira_secret_names(user_id)
        ok = True
        for field, value in fields.items():
            ok = self._secrets.put(
                names[field],
                value.strip(),
                caller=caller,
                reason=f"credentials.put.jira.{field}",
                user_id=user_id,
                kind=JIRA_KIND,
            ) and ok
        return ok

    def get_github_pat(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        caller: str = "",
        invocation_id: str = "",
    ) -> str:
        """Fetch the user's GH PAT for one invocation. Returns "" if absent."""
        return self._secrets.get(
            _secret_name(GH_PAT_KIND, user_id),
            default="",
            caller=caller,
            reason=f"credentials.access.github:invocation={invocation_id}",
        )

    def inject_for_invocation(
        self,
        invoke_ctx: dict[str, Any],
        *,
        user_id: str = DEFAULT_USER_ID,
        caller: str = "",
        invocation_id: str = "",
    ) -> None:
        """Populate invoke_ctx with secrets agent tools can pull at call time.

        The agent's tool handlers (e.g. dev_tools._handle_gh_open_pr) read
        these from `agent_context`. The secrets never land on os.environ,
        so concurrent invocations don't race.
        """
        pat = self.get_github_pat(
            user_id=user_id, caller=caller, invocation_id=invocation_id,
        )
        if pat:
            invoke_ctx.setdefault("_credentials", {})["gh_token"] = pat

    # -- three-tier scoped secrets -------------------------------------------

    def put_scoped_secret(
        self,
        name: str,
        value: str,
        *,
        scope: str = SCOPE_USER,
        namespace: str | None = None,
        user_id: str = DEFAULT_USER_ID,
        kind: str = "generic",
        caller: str = "",
    ) -> bool:
        """Store a logical secret at a tier (platform/namespace/user).

        The stored key is scope-qualified (see :func:`scoped_secret_name`); a
        manifest/MCP config references it as the **logical** name and the
        resolver expands it. Authorization is the API layer's job, not here.
        """
        scope = _canonical_scope(scope)
        if scope not in SCOPES:
            raise ValueError(f"unknown secret scope: {scope!r}")
        if scope == SCOPE_NAMESPACE and not namespace:
            raise ValueError("namespace-scoped secret requires a namespace")
        if value is None or not str(value).strip():
            raise ValueError(f"secret '{name}' value is empty")
        name = validate_secret_name(name)
        stored = scoped_secret_name(name, scope=scope, namespace=namespace, user_id=user_id)
        return self._secrets.put(
            stored,
            str(value).strip(),
            caller=caller,
            reason=f"credentials.put.{scope}.{kind}",
            user_id=user_id,
            kind=kind,
            scope=scope,
            namespace=namespace if scope == SCOPE_NAMESPACE else None,
        )

    def delete_scoped_secret(
        self,
        name: str,
        *,
        scope: str = SCOPE_USER,
        namespace: str | None = None,
        user_id: str = DEFAULT_USER_ID,
        caller: str = "",
    ) -> bool:
        """Delete a scoped logical secret. Idempotent."""
        scope = _canonical_scope(scope)
        if scope not in SCOPES:
            raise ValueError(f"unknown secret scope: {scope!r}")
        name = validate_secret_name(name)
        stored = scoped_secret_name(name, scope=scope, namespace=namespace, user_id=user_id)
        return self._secrets.delete(stored, caller=caller, reason=f"credentials.delete.{scope}")

    def list_secrets(
        self,
        *,
        scope: str = SCOPE_USER,
        namespace: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List logical secret names at a tier (names + metadata, never values)."""
        scope = _canonical_scope(scope)
        if scope not in SCOPES:
            raise ValueError(f"unknown secret scope: {scope!r}")
        rows = self._secrets.list_names(
            scope=scope,
            namespace=namespace,
            user_id=user_id,
            gcp_prefix=scope_prefix(scope, namespace=namespace, user_id=user_id),
        )
        out: list[dict[str, Any]] = []
        for r in rows or []:
            stored = r.get("secret_name", "")
            out.append({
                "name": logical_secret_name(
                    stored, scope=scope, namespace=r.get("namespace") or namespace, user_id=r.get("user_id") or user_id,
                ),
                "kind": r.get("kind", "generic"),
                "scope": r.get("scope", scope),
                "namespace": r.get("namespace"),
            })
        return out

    def resolve(
        self,
        ref: str,
        *,
        namespace: str | None = None,
        user_id: str = DEFAULT_USER_ID,
        order: tuple[str, ...] = (SCOPE_USER, SCOPE_NAMESPACE, SCOPE_TENANT),
        allowed_scopes: tuple[str, ...] | None = None,
        caller: str = "",
    ) -> str | None:
        """Resolve a logical secret reference to its value across the tiers.

        ``ref`` forms:
          * ``tenant/<name>`` (or legacy ``platform/<name>``) / ``ns/<name>`` /
            ``user/<name>`` — explicit pin.
          * ``<name>`` — walk ``order`` (default user→namespace→tenant),
            first hit wins; then a final literal/legacy lookup (covers
            ``forgeos-jira-token-<user>`` and env fallback).

        ``allowed_scopes`` restricts which tiers may be read (``None`` = all).
        It is the runtime authorization boundary: a SHARED/namespace agent is
        resolved with ``(SCOPE_NAMESPACE, SCOPE_TENANT)`` so it can never read a
        user-scoped secret — not via the walk, and not via an explicit
        ``user/<name>`` pin. See :func:`allowed_scopes_for_agent`.
        Returns ``None`` when nothing resolves.
        """
        ref = (ref or "").strip()
        if not ref:
            return None
        allowed = set(allowed_scopes) if allowed_scopes is not None else None
        pins = {
            "tenant/": SCOPE_TENANT, "platform/": SCOPE_TENANT,  # legacy alias
            "ns/": SCOPE_NAMESPACE, "user/": SCOPE_USER,
        }
        for prefix, scope in pins.items():
            if ref.startswith(prefix):
                if allowed is not None and scope not in allowed:
                    return None  # out-of-scope pin is refused, not honored
                stored = scoped_secret_name(
                    ref[len(prefix):], scope=scope, namespace=namespace, user_id=user_id
                )
                return self._secrets.get(
                    stored, default="", caller=caller, reason=f"credentials.resolve.{scope}",
                    allow_env=False,
                ) or None
        for scope in order:
            if allowed is not None and scope not in allowed:
                continue
            if scope == SCOPE_NAMESPACE and not namespace:
                continue
            if scope == SCOPE_USER and not user_id:
                continue
            stored = scoped_secret_name(ref, scope=scope, namespace=namespace, user_id=user_id)
            val = self._secrets.get(
                stored, default="", caller=caller, reason=f"credentials.resolve.{scope}",
                allow_env=False,
            )
            if val:
                return val
        # Final fallback: treat ref as a literal/legacy stored name (+ env).
        return self._secrets.get(
            ref, default="", caller=caller, reason="credentials.resolve.literal",
        ) or None
