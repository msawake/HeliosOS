"""OAuth 2.0 authorization-server storage for the Helios MCP endpoint.

Helios is the authorization server for its own MCP endpoint: MCP clients
(Claude Code, Cursor, …) discover it, dynamically register (RFC 7591), run an
authorization-code + PKCE flow, and receive access/refresh tokens. This module
is the persistence + crypto layer behind ``forgeos_web/oauth/views.py``.

It deliberately mirrors ``src/api/personal_tokens.py`` — opaque, prefixed,
sha256-hashed tokens resolved cross-tenant via ``db.admin()`` — so OAuth access
tokens (``hoat_``) authenticate on the exact same Bearer path as PATs. There is
no Django ORM model (PATs have none either); everything goes through the
platform ``DatabaseClient`` so the connection's ``app.current_tenant`` drives
RLS.

Like the other platform stores (``src/platform/client_store.py``), each store
FALLS BACK to a process-local in-memory backend when no database is connected,
so it works in the DB-less web tests and in local dev without Postgres.

Three concerns, three stores:
  * ``OAuthClientStore``        — registered clients (global; DCR is unauthenticated)
  * ``OAuthAuthorizationStore`` — pending consent requests + one-time auth codes (global)
  * ``OAuthTokenStore``         — access + refresh tokens (tenant-scoped, RLS)
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.api.personal_tokens import TokenIdentity

logger = logging.getLogger(__name__)

# Prefixes — visually distinguishable in logs/DB, mirroring ``hpat_`` (PATs).
CLIENT_ID_PREFIX = "hoc_"
CLIENT_SECRET_PREFIX = "hocs_"
ACCESS_TOKEN_PREFIX = "hoat_"
REFRESH_TOKEN_PREFIX = "hort_"

# Lifetimes.
AUTH_REQUEST_TTL = timedelta(minutes=10)   # user has 10 min to complete consent
AUTH_CODE_TTL = timedelta(minutes=5)       # code must be exchanged within 5 min
ACCESS_TOKEN_TTL = timedelta(hours=1)      # short-lived; refreshed via refresh_token
REFRESH_TOKEN_TTL = timedelta(days=30)     # long-lived; rotated on every use

DEFAULT_SCOPE = "mcp"

# Process-local fallback backend (used only when no DB is connected).
_MEM: dict[str, Any] = {
    "clients": {},   # client_id -> dict
    "requests": {},  # request_id -> dict
    "codes": {},     # code_hash -> dict
    "access": [],    # list[dict]
    "refresh": [],   # list[dict]
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mint(prefix: str) -> str:
    """A fresh opaque secret: ``prefix`` + 32 URL-safe random bytes."""
    return prefix + secrets.token_urlsafe(32)


def _display_prefix(plaintext: str, prefix: str) -> str:
    return plaintext[: len(prefix) + 4] + "…"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """RFC 7636 S256: BASE64URL(SHA256(verifier)) == challenge, constant-time.

    S256 is the only method Helios advertises/accepts (``plain`` is refused at
    the authorize step), so we only implement S256 here.
    """
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


# --------------------------------------------------------------------------- #
# Clients (Dynamic Client Registration, RFC 7591)
# --------------------------------------------------------------------------- #
class OAuthClientStore:
    """Registered OAuth clients. Global (no tenant): registration is
    unauthenticated so no tenant is known yet — the user's tenant is bound on
    the tokens minted after consent, not on the client."""

    def __init__(self, db_client: Any) -> None:
        self._db = db_client

    @property
    def available(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    def register(
        self,
        *,
        client_name: str,
        redirect_uris: list[str],
        grant_types: list[str] | None = None,
        token_endpoint_auth_method: str = "none",
        scope: str = DEFAULT_SCOPE,
    ) -> dict:
        """Create a client and return its registration (incl. secret if confidential)."""
        client_id = _mint(CLIENT_ID_PREFIX)
        confidential = token_endpoint_auth_method != "none"
        secret_plain = _mint(CLIENT_SECRET_PREFIX) if confidential else None
        secret_hash = _hash(secret_plain) if secret_plain else None
        grants = grant_types or ["authorization_code", "refresh_token"]
        if self.available:
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO oauth_clients "
                    "(client_id, client_secret_hash, client_name, redirect_uris, "
                    " grant_types, token_endpoint_auth_method, scope) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (client_id, secret_hash, client_name, redirect_uris,
                     grants, token_endpoint_auth_method, scope),
                )
                conn.commit()
        else:
            _MEM["clients"][client_id] = {
                "client_id": client_id, "client_secret_hash": secret_hash,
                "client_name": client_name, "redirect_uris": list(redirect_uris),
                "grant_types": grants, "token_endpoint_auth_method": token_endpoint_auth_method,
                "scope": scope,
            }
        out = {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "grant_types": grants,
            "token_endpoint_auth_method": token_endpoint_auth_method,
            "scope": scope,
        }
        if secret_plain:
            out["client_secret"] = secret_plain  # returned ONCE
        return out

    def get(self, client_id: str) -> dict | None:
        if not client_id:
            return None
        if not self.available:
            row = _MEM["clients"].get(client_id)
            return dict(row) if row else None
        try:
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "SELECT client_id, client_secret_hash, client_name, "
                    "redirect_uris, grant_types, token_endpoint_auth_method, scope "
                    "FROM oauth_clients WHERE client_id = %s",
                    (client_id,),
                )
        except Exception:
            logger.exception("OAuthClientStore.get failed")
            return None
        return dict(row) if row else None

    def verify_secret(self, client: dict, secret: str | None) -> bool:
        """True if the client is public (no secret) or the secret matches."""
        stored = client.get("client_secret_hash")
        if not stored:
            return True  # public / PKCE-only client
        if not secret:
            return False
        return secrets.compare_digest(_hash(secret), stored)


# --------------------------------------------------------------------------- #
# Pending consent requests + one-time authorization codes
# --------------------------------------------------------------------------- #
class OAuthAuthorizationStore:
    """Parked /authorize requests (awaiting consent) and issued auth codes.

    Global (no RLS): the /token exchange is unauthenticated and cross-tenant,
    resolved by hash via db.admin() — the same shape as PAT verification. The
    consenting user's tenant/user_id are plain columns on the code row.
    """

    def __init__(self, db_client: Any) -> None:
        self._db = db_client

    @property
    def available(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    # -- request (pre-consent) --------------------------------------------- #
    def create_request(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scope: str,
        state: str | None,
    ) -> str:
        request_id = uuid.uuid4().hex
        expires_at = _now() + AUTH_REQUEST_TTL
        if self.available:
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO oauth_authorization_requests "
                    "(request_id, client_id, redirect_uri, code_challenge, "
                    " code_challenge_method, scope, state, expires_at) "
                    "VALUES (%s, %s, %s, %s, 'S256', %s, %s, %s)",
                    (request_id, client_id, redirect_uri, code_challenge,
                     scope, state, expires_at),
                )
                conn.commit()
        else:
            _MEM["requests"][request_id] = {
                "request_id": request_id, "client_id": client_id,
                "redirect_uri": redirect_uri, "code_challenge": code_challenge,
                "scope": scope, "state": state, "expires_at": expires_at,
            }
        return request_id

    def get_request(self, request_id: str) -> dict | None:
        if not request_id:
            return None
        if not self.available:
            row = _MEM["requests"].get(request_id)
            if not row or row["expires_at"] <= _now():
                return None
            client = _MEM["clients"].get(row["client_id"]) or {}
            return {**row, "client_name": client.get("client_name", "")}
        try:
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "SELECT r.request_id, r.client_id, r.redirect_uri, "
                    "       r.code_challenge, r.scope, r.state, r.expires_at, "
                    "       c.client_name "
                    "FROM oauth_authorization_requests r "
                    "JOIN oauth_clients c ON c.client_id = r.client_id "
                    "WHERE r.request_id = %s AND r.expires_at > NOW()",
                    (request_id,),
                )
        except Exception:
            logger.exception("OAuthAuthorizationStore.get_request failed")
            return None
        return dict(row) if row else None

    def delete_request(self, request_id: str) -> None:
        if not self.available:
            _MEM["requests"].pop(request_id, None)
            return
        try:
            with self._db.admin() as conn:
                conn.execute(
                    "DELETE FROM oauth_authorization_requests WHERE request_id = %s",
                    (request_id,),
                )
                conn.commit()
        except Exception:
            logger.exception("OAuthAuthorizationStore.delete_request failed")

    # -- code (post-consent) ----------------------------------------------- #
    def issue_code(
        self,
        *,
        client_id: str,
        tenant_id: str,
        user_id: str,
        redirect_uri: str,
        code_challenge: str,
        scope: str,
    ) -> str:
        """Mint a single-use authorization code; return the PLAINTEXT code."""
        code = secrets.token_urlsafe(32)
        expires_at = _now() + AUTH_CODE_TTL
        if self.available:
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO oauth_authorization_codes "
                    "(code_hash, client_id, tenant_id, user_id, redirect_uri, "
                    " code_challenge, code_challenge_method, scope, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'S256', %s, %s)",
                    (_hash(code), client_id, tenant_id, user_id, redirect_uri,
                     code_challenge, scope, expires_at),
                )
                conn.commit()
        else:
            _MEM["codes"][_hash(code)] = {
                "client_id": client_id, "tenant_id": tenant_id, "user_id": user_id,
                "redirect_uri": redirect_uri, "code_challenge": code_challenge,
                "scope": scope, "expires_at": expires_at, "consumed_at": None,
            }
        return code

    def consume_code(self, code: str) -> dict | None:
        """Atomically mark a code consumed and return its binding, or None.

        The ``consumed_at IS NULL AND expires_at > NOW()`` guard in the UPDATE
        makes redemption single-use and race-free (a replayed code updates zero
        rows and returns nothing).
        """
        if not code:
            return None
        if not self.available:
            row = _MEM["codes"].get(_hash(code))
            if not row or row["consumed_at"] is not None or row["expires_at"] <= _now():
                return None
            row["consumed_at"] = _now()
            return {k: row[k] for k in
                    ("client_id", "tenant_id", "user_id", "redirect_uri", "code_challenge", "scope")}
        try:
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "UPDATE oauth_authorization_codes SET consumed_at = NOW() "
                    "WHERE code_hash = %s AND consumed_at IS NULL "
                    "AND expires_at > NOW() "
                    "RETURNING client_id, tenant_id, user_id, redirect_uri, "
                    "          code_challenge, scope",
                    (_hash(code),),
                )
                conn.commit()
        except Exception:
            logger.exception("OAuthAuthorizationStore.consume_code failed")
            return None
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Access + refresh tokens (tenant-scoped, RLS — mirror of PersonalTokenStore)
# --------------------------------------------------------------------------- #
class OAuthTokenStore:
    def __init__(self, db_client: Any, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id

    @property
    def available(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    # -- mint -------------------------------------------------------------- #
    def issue_pair(
        self, *, tenant_id: str, user_id: str, client_id: str, scope: str
    ) -> dict:
        """Mint an access + refresh token for a user. Returns the plaintext pair."""
        access = _mint(ACCESS_TOKEN_PREFIX)
        refresh = _mint(REFRESH_TOKEN_PREFIX)
        now = _now()
        if self.available:
            with self._db.tenant(tenant_id) as conn:
                conn.execute(
                    "INSERT INTO oauth_access_tokens "
                    "(id, tenant_id, user_id, client_id, token_hash, prefix, scope, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), tenant_id, user_id, client_id, _hash(access),
                     _display_prefix(access, ACCESS_TOKEN_PREFIX), scope,
                     now + ACCESS_TOKEN_TTL),
                )
                conn.execute(
                    "INSERT INTO oauth_refresh_tokens "
                    "(id, tenant_id, user_id, client_id, token_hash, prefix, scope, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (str(uuid.uuid4()), tenant_id, user_id, client_id, _hash(refresh),
                     _display_prefix(refresh, REFRESH_TOKEN_PREFIX), scope,
                     now + REFRESH_TOKEN_TTL),
                )
                conn.commit()
        else:
            _MEM["access"].append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "user_id": user_id,
                "client_id": client_id, "token_hash": _hash(access),
                "prefix": _display_prefix(access, ACCESS_TOKEN_PREFIX), "scope": scope,
                "created_at": now, "last_used_at": None,
                "expires_at": now + ACCESS_TOKEN_TTL, "revoked_at": None,
            })
            _MEM["refresh"].append({
                "id": str(uuid.uuid4()), "tenant_id": tenant_id, "user_id": user_id,
                "client_id": client_id, "token_hash": _hash(refresh),
                "prefix": _display_prefix(refresh, REFRESH_TOKEN_PREFIX), "scope": scope,
                "created_at": now, "last_used_at": None,
                "expires_at": now + REFRESH_TOKEN_TTL, "revoked_at": None,
            })
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
            "scope": scope,
        }

    def rotate_refresh(self, refresh_token: str, *, client_id: str | None = None) -> dict | None:
        """Validate a refresh token, revoke it, and mint a fresh pair (rotation).

        Returns the new token pair, or None if the refresh token is unknown,
        revoked, expired, or (when ``client_id`` is given) was issued to a
        different client. Lookup is cross-tenant (db.admin) by hash, then the
        old token is revoked + the new pair minted under the resolved tenant.
        """
        if not refresh_token or not refresh_token.startswith(REFRESH_TOKEN_PREFIX):
            return None
        token_hash = _hash(refresh_token)
        if self.available:
            try:
                with self._db.admin() as conn:
                    row = conn.execute_one(
                        "SELECT id, tenant_id, user_id, client_id, scope, "
                        "       expires_at, revoked_at "
                        "FROM oauth_refresh_tokens WHERE token_hash = %s",
                        (token_hash,),
                    )
            except Exception:
                logger.exception("OAuthTokenStore.rotate_refresh lookup failed")
                return None
        else:
            row = next((r for r in _MEM["refresh"] if r["token_hash"] == token_hash), None)
        if not row or row.get("revoked_at") is not None:
            return None
        if client_id is not None and str(row.get("client_id")) != client_id:
            return None
        exp = row.get("expires_at")
        if exp is not None and exp.timestamp() < time.time():
            return None
        tenant_id = str(row["tenant_id"])
        # Revoke the used refresh token, then issue a fresh pair (rotation).
        if self.available:
            try:
                with self._db.tenant(tenant_id) as conn:
                    conn.execute(
                        "UPDATE oauth_refresh_tokens SET revoked_at = NOW() WHERE id = %s",
                        (row["id"],),
                    )
                    conn.commit()
            except Exception:
                logger.exception("OAuthTokenStore.rotate_refresh revoke failed")
                return None
        else:
            row["revoked_at"] = _now()
        return self.issue_pair(
            tenant_id=tenant_id,
            user_id=str(row["user_id"]),
            client_id=str(row["client_id"]),
            scope=row.get("scope") or DEFAULT_SCOPE,
        )

    # -- verify (called on every Bearer request via AuthManager) ----------- #
    def verify_access_token(self, token: str) -> TokenIdentity | None:
        """Resolve an ``hoat_`` access token to its owner's identity, or None.

        Same shape as PersonalTokenStore.verify: cross-tenant hashed lookup via
        db.admin(), reject revoked/expired, best-effort touch last_used_at.
        """
        if not token or not token.startswith(ACCESS_TOKEN_PREFIX):
            return None
        token_hash = _hash(token)
        if not self.available:
            row = next((r for r in _MEM["access"] if r["token_hash"] == token_hash), None)
            if not row or row.get("revoked_at") is not None:
                return None
            if row["expires_at"] is not None and row["expires_at"].timestamp() < time.time():
                return None
            row["last_used_at"] = _now()
            return TokenIdentity(
                token_id=row["id"], user_id=str(row["user_id"]),
                tenant_id=str(row["tenant_id"]), role="viewer", name="", email="",
            )
        try:
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "SELECT t.id AS token_id, t.tenant_id, t.user_id, "
                    "       t.expires_at, t.revoked_at, "
                    "       u.email, u.role, u.name "
                    "FROM oauth_access_tokens t "
                    "LEFT JOIN tenant_users u "
                    "  ON u.id = t.user_id AND u.tenant_id = t.tenant_id "
                    "WHERE t.token_hash = %s",
                    (token_hash,),
                )
        except Exception:
            logger.exception("OAuthTokenStore.verify_access_token lookup failed")
            return None
        if not row or row.get("revoked_at") is not None:
            return None
        exp = row.get("expires_at")
        if exp is not None:
            try:
                if exp.timestamp() < time.time():
                    return None
            except Exception:
                pass
        try:
            with self._db.tenant(row["tenant_id"]) as conn:
                conn.execute(
                    "UPDATE oauth_access_tokens SET last_used_at = NOW() WHERE id = %s",
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

    def revoke_by_value(self, token: str) -> bool:
        """RFC 7009 revocation by token value (access or refresh). Best-effort,
        cross-tenant by hash. Returns True if a live row was revoked."""
        if not token:
            return False
        if token.startswith(ACCESS_TOKEN_PREFIX):
            table, mem_key = "oauth_access_tokens", "access"
        elif token.startswith(REFRESH_TOKEN_PREFIX):
            table, mem_key = "oauth_refresh_tokens", "refresh"
        else:
            return False
        token_hash = _hash(token)
        if not self.available:
            hit = False
            for r in _MEM[mem_key]:
                if r["token_hash"] == token_hash and r["revoked_at"] is None:
                    r["revoked_at"] = _now()
                    hit = True
            return hit
        try:
            with self._db.admin() as conn:
                rc = conn.execute(
                    f"UPDATE {table} SET revoked_at = NOW() "  # noqa: S608 (table is a fixed literal)
                    "WHERE token_hash = %s AND revoked_at IS NULL",
                    (token_hash,),
                )
                conn.commit()
            return bool(rc)
        except Exception:
            logger.exception("OAuthTokenStore.revoke_by_value failed")
            return False

    # -- grant management (dashboard) -------------------------------------- #
    def list_grants_for_user(self, user_id: str) -> list[dict]:
        """One row per client the user currently has live (non-revoked) tokens for."""
        if not self.available:
            grants: dict[str, dict] = {}
            for r in _MEM["access"]:
                if (r["tenant_id"] == self._tenant_id and str(r["user_id"]) == str(user_id)
                        and r["revoked_at"] is None):
                    client = _MEM["clients"].get(r["client_id"]) or {}
                    g = grants.setdefault(r["client_id"], {
                        "client_id": r["client_id"],
                        "client_name": client.get("client_name", ""),
                        "granted_at": r["created_at"], "last_used_at": r["last_used_at"],
                    })
                    g["granted_at"] = max(g["granted_at"], r["created_at"])
            return sorted(grants.values(), key=lambda g: g["granted_at"], reverse=True)
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rows = conn.execute(
                    "SELECT t.client_id, c.client_name, "
                    "       MAX(t.created_at) AS granted_at, "
                    "       MAX(t.last_used_at) AS last_used_at "
                    "FROM oauth_access_tokens t "
                    "LEFT JOIN oauth_clients c ON c.client_id = t.client_id "
                    "WHERE t.tenant_id = %s AND t.user_id = %s AND t.revoked_at IS NULL "
                    "GROUP BY t.client_id, c.client_name "
                    "ORDER BY granted_at DESC",
                    (self._tenant_id, user_id),
                )
            return [
                {
                    "client_id": r["client_id"],
                    "client_name": r.get("client_name") or "",
                    "granted_at": r["granted_at"],
                    "last_used_at": r["last_used_at"],
                }
                for r in (rows or [])
            ]
        except Exception:
            logger.exception("OAuthTokenStore.list_grants_for_user failed")
            return []

    def revoke_grant(self, user_id: str, client_id: str) -> bool:
        """Revoke ALL of a user's access + refresh tokens for one client."""
        if not self.available:
            hit = False
            for key in ("access", "refresh"):
                for r in _MEM[key]:
                    if (r["tenant_id"] == self._tenant_id and str(r["user_id"]) == str(user_id)
                            and r["client_id"] == client_id and r["revoked_at"] is None):
                        r["revoked_at"] = _now()
                        if key == "access":
                            hit = True
            return hit
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rc1 = conn.execute(
                    "UPDATE oauth_access_tokens SET revoked_at = NOW() "
                    "WHERE tenant_id = %s AND user_id = %s AND client_id = %s "
                    "AND revoked_at IS NULL",
                    (self._tenant_id, user_id, client_id),
                )
                conn.execute(
                    "UPDATE oauth_refresh_tokens SET revoked_at = NOW() "
                    "WHERE tenant_id = %s AND user_id = %s AND client_id = %s "
                    "AND revoked_at IS NULL",
                    (self._tenant_id, user_id, client_id),
                )
                conn.commit()
            return bool(rc1)
        except Exception:
            logger.exception("OAuthTokenStore.revoke_grant failed")
            return False


__all__ = [
    "OAuthClientStore",
    "OAuthAuthorizationStore",
    "OAuthTokenStore",
    "verify_pkce_s256",
    "ACCESS_TOKEN_PREFIX",
    "REFRESH_TOKEN_PREFIX",
    "DEFAULT_SCOPE",
    "AUTH_CODE_TTL",
    "ACCESS_TOKEN_TTL",
]
