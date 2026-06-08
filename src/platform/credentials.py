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
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_USER_ID = "default"
GH_PAT_KIND = "github"
JIRA_KIND = "jira"


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
