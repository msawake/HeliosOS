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


def _secret_name(kind: str, user_id: str) -> str:
    user_id = user_id or DEFAULT_USER_ID
    return f"forgeos-{kind}-pat-{user_id}"


class CredentialStore:
    """Thin wrapper around SecretsManager for per-user developer credentials."""

    def __init__(self, secrets_manager: Any) -> None:
        self._secrets = secrets_manager

    def put_github_pat(self, pat: str, *, user_id: str = DEFAULT_USER_ID, caller: str = "") -> bool:
        if not pat or not pat.strip():
            raise ValueError("PAT is empty")
        return self._secrets.put(
            _secret_name(GH_PAT_KIND, user_id),
            pat.strip(),
            caller=caller,
            reason="credentials.put.github",
        )

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
