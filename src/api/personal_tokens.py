"""Personal Access Tokens (PATs) — long-lived, revocable bearer tokens.

Users create one from the dashboard ("Settings → MCP Access") and paste it
into their MCP client (Claude Code, Cursor, …) via ``claude mcp add
--header 'Authorization: Bearer hpat_...' ...``. AuthManager recognizes the
``hpat_`` prefix on Bearer tokens and looks the hash up in
``personal_access_tokens`` — matching the token gives the caller the same
identity the token's owner has (user_id + tenant_id + role).

The plaintext token is returned to the caller ONCE, at creation time; the
DB only stores its SHA-256. A dumped table is not usable as credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Prefix chosen so both users and log-scrapers can visually spot a Helios OS
# PAT and distinguish it from a session token (v1.<payload>.<sig>) or a
# platform API key (opaque).
TOKEN_PREFIX = "hpat_"


@dataclass
class TokenIdentity:
    """The identity a valid PAT resolves to."""
    token_id: str
    user_id: str
    tenant_id: str
    role: str
    name: str
    email: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mint() -> str:
    """Return a fresh ``hpat_`` token — 32 URL-safe random bytes after the prefix."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


class PersonalTokenStore:
    """Postgres-backed store for personal access tokens.

    Tenant scoping is enforced by RLS via ``db.tenant(tenant_id)`` — each
    call sets ``app.current_tenant`` for the connection, and the
    ``tenant_isolation_pat`` policy filters rows accordingly.
    """

    def __init__(self, db_client: Any, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id

    @property
    def available(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    # ------------------------------------------------------------------ create
    def create(
        self,
        user_id: str,
        name: str,
        *,
        expires_at: str | None = None,
    ) -> dict:
        """Mint a new PAT for ``user_id`` and return {token, id, prefix, name, ...}.

        ``token`` is the PLAINTEXT and is returned only from this call — the
        caller MUST surface it to the user immediately and never store it.
        """
        if not self.available:
            raise RuntimeError("database not connected — cannot mint personal tokens")
        name = (name or "").strip()
        if not name:
            raise ValueError("token name is required")
        plaintext = _mint()
        token_hash = _hash(plaintext)
        # Store a short display prefix so listings can show something identifying
        # ("hpat_abcd…") without revealing the full token.
        prefix = plaintext[: len(TOKEN_PREFIX) + 4] + "…"
        token_id = str(uuid.uuid4())
        try:
            with self._db.tenant(self._tenant_id) as conn:
                conn.execute(
                    "INSERT INTO personal_access_tokens "
                    "(id, tenant_id, user_id, name, token_hash, prefix, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (token_id, self._tenant_id, user_id, name,
                     token_hash, prefix, expires_at),
                )
                conn.commit()
        except Exception as e:
            # Most likely: unique(tenant_id, user_id, name) — friendlier error.
            msg = str(e).lower()
            if "unique" in msg or "duplicate" in msg:
                raise ValueError(
                    f"a token named {name!r} already exists — pick another name or revoke it"
                )
            raise
        return {
            "id": token_id,
            "name": name,
            "prefix": prefix,
            "expires_at": expires_at,
            "token": plaintext,  # returned ONCE
        }

    # -------------------------------------------------------------------- list
    def list_for_user(self, user_id: str) -> list[dict]:
        """Return active + revoked tokens for a user (never the plaintext)."""
        if not self.available:
            return []
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rows = conn.execute(
                    "SELECT id, name, prefix, created_at, last_used_at, "
                    "expires_at, revoked_at "
                    "FROM personal_access_tokens "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "ORDER BY created_at DESC",
                    (self._tenant_id, user_id),
                )
            return [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "prefix": r["prefix"],
                    "created_at": r["created_at"],
                    "last_used_at": r["last_used_at"],
                    "expires_at": r["expires_at"],
                    "revoked_at": r["revoked_at"],
                }
                for r in (rows or [])
            ]
        except Exception:
            logger.exception("PersonalTokenStore.list_for_user failed")
            return []

    # ------------------------------------------------------------------ revoke
    def revoke(self, user_id: str, token_id: str) -> bool:
        """Mark a PAT revoked. Returns True if it existed and wasn't already revoked."""
        if not self.available:
            return False
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rc = conn.execute(
                    "UPDATE personal_access_tokens SET revoked_at = NOW() "
                    "WHERE tenant_id = %s AND user_id = %s AND id = %s "
                    "AND revoked_at IS NULL",
                    (self._tenant_id, user_id, token_id),
                )
                conn.commit()
            return bool(rc)
        except Exception:
            logger.exception("PersonalTokenStore.revoke failed")
            return False

    # --------------------------------------------------------------- verify
    def verify(self, token: str) -> TokenIdentity | None:
        """Look up a plaintext token; return the identity or None.

        Constant-time comparison at the SQL layer (``= %s`` on the hashed
        column). Rejects tokens missing the prefix, revoked, or past their
        expiry. On a match, bumps ``last_used_at`` (best-effort, non-blocking
        on failure so an audit-timestamp problem never breaks auth).
        """
        if not self.available or not token or not token.startswith(TOKEN_PREFIX):
            return None
        token_hash = _hash(token)
        try:
            # Cross-tenant lookup: an incoming Bearer doesn't tell us the
            # tenant until we resolve the row. Use db.admin() (bypasses RLS),
            # then re-scope subsequent ops (touch) with the resolved tenant.
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "SELECT t.id AS token_id, t.tenant_id, t.user_id, "
                    "       t.expires_at, t.revoked_at, "
                    "       u.email, u.role, u.name "
                    "FROM personal_access_tokens t "
                    "LEFT JOIN tenant_users u "
                    "  ON u.id = t.user_id AND u.tenant_id = t.tenant_id "
                    "WHERE t.token_hash = %s",
                    (token_hash,),
                )
        except Exception:
            logger.exception("PersonalTokenStore.verify lookup failed")
            return None
        if not row:
            return None
        if row.get("revoked_at") is not None:
            return None
        exp = row.get("expires_at")
        if exp is not None:
            try:
                # exp is a datetime; compare epoch to seconds since epoch
                if exp.timestamp() < time.time():
                    return None
            except Exception:
                pass
        # Best-effort touch of last_used_at — never fatal.
        try:
            with self._db.tenant(row["tenant_id"]) as conn:
                conn.execute(
                    "UPDATE personal_access_tokens SET last_used_at = NOW() "
                    "WHERE id = %s",
                    (row["token_id"],),
                )
                conn.commit()
        except Exception:
            pass
        return TokenIdentity(
            token_id=str(row["token_id"]),
            user_id=str(row["user_id"]),
            tenant_id=str(row["tenant_id"]),
            role=row.get("role") or "viewer",
            name=row.get("name") or "",
            email=row.get("email") or "",
        )


__all__ = ["PersonalTokenStore", "TokenIdentity", "TOKEN_PREFIX"]
