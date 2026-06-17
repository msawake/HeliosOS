"""
Authentication and authorization for Helios OS SaaS.

Supports:
- Firebase Auth (JWT tokens) for dashboard users
- API key auth for programmatic access
- Role-based access control (Admin, Operator, Viewer)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local password hashing (stdlib pbkdf2 — no bcrypt/argon2 dependency)
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Hash a password as ``pbkdf2_sha256$<iters>$<b64salt>$<b64hash>``."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password_hash(password: str, stored: str | None) -> bool:
    """Constant-time verify against a stored hash. False on NULL/malformed
    (Firebase-only rows and the env admin-key principal have no password)."""
    if not stored:
        return False
    try:
        algo, iters, b64salt, b64dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(b64salt)
        expected = base64.b64decode(b64dk)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Session token signing secret
# ---------------------------------------------------------------------------

_SESSION_SECRET_ENV = "FORGEOS_SESSION_SECRET"
_SESSION_DEV_SALT = b"forgeos-local-dev-session-secret-v1"


def _load_session_secret() -> bytes:
    """Return the HMAC secret for signed session tokens. Prefers
    ``FORGEOS_SESSION_SECRET``; falls back to a STABLE derived dev key (loud
    warning) so local dev works with zero config. Never random-at-boot (that
    would invalidate live tokens on every restart)."""
    raw = os.environ.get(_SESSION_SECRET_ENV, "").strip()
    if raw:
        return raw.encode("utf-8")
    logger.warning(
        "%s not set — using an INSECURE derived dev key for session tokens. "
        "Set %s to a strong random value in any real deployment.",
        _SESSION_SECRET_ENV, _SESSION_SECRET_ENV,
    )
    return hashlib.sha256(_SESSION_DEV_SALT).digest()

# ---------------------------------------------------------------------------
# Auth rate limiting -- blocks IPs after too many failed attempts
# ---------------------------------------------------------------------------

_AUTH_FAIL_WINDOW = 60      # seconds
_AUTH_FAIL_MAX = 10         # max failures per window
_AUTH_BLOCK_DURATION = 300  # block for 5 minutes

_auth_failures: dict[str, list[float]] = defaultdict(list)
_auth_blocks: dict[str, float] = {}


def _check_auth_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed to attempt auth, False if blocked."""
    now = time.time()
    # Check if currently blocked
    if ip in _auth_blocks:
        if now < _auth_blocks[ip]:
            return False
        else:
            del _auth_blocks[ip]

    return True


def _record_auth_failure(ip: str) -> None:
    """Record a failed auth attempt from an IP, potentially blocking it."""
    now = time.time()
    # Clean old entries
    _auth_failures[ip] = [t for t in _auth_failures[ip] if now - t < _AUTH_FAIL_WINDOW]
    _auth_failures[ip].append(now)

    if len(_auth_failures[ip]) >= _AUTH_FAIL_MAX:
        _auth_blocks[ip] = now + _AUTH_BLOCK_DURATION
        _auth_failures[ip] = []
        logger.warning("Blocked IP %s for %ds after %d failed auth attempts",
                        ip, _AUTH_BLOCK_DURATION, _AUTH_FAIL_MAX)

# Try Firebase Admin SDK
try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth, credentials
    HAS_FIREBASE = True
except ImportError:
    HAS_FIREBASE = False


class UserRole:
    ADMIN = "admin"       # Full access: manage agents, approve, configure
    OPERATOR = "operator"  # Approve/reject HITL, monitor workflows
    VIEWER = "viewer"      # Read-only access to dashboards


class AuthUser:
    """Authenticated user context."""

    def __init__(
        self,
        user_id: str,
        email: str,
        tenant_id: str,
        role: str = UserRole.VIEWER,
        name: str = "",
    ):
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.role = role
        self.name = name

    def can_approve(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.OPERATOR)

    def can_configure(self) -> bool:
        return self.role == UserRole.ADMIN

    def can_view(self) -> bool:
        return True

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "name": self.name,
        }


class AuthManager:
    """Manages authentication via Firebase or API keys."""

    def __init__(self, db_client=None, *, tenant_id: str | None = None):
        self._db = db_client
        self._firebase_initialized = False
        # Bound tenant for the env-provisioned admin key (below). Defaults to
        # the boot tenant; the standalone fallback keeps legacy callers working.
        self._tenant_id = tenant_id or os.environ.get("FORGEOS_TENANT_ID") or "default"
        # Platform admin API key, provisioned out-of-band (Pulumi → Secret
        # Manager → FORGEOS_ADMIN_API_KEY env). Recognized as an ``admin``
        # principal so auth-enabled deployments have a usable credential without
        # seeding a DB row or wiring Firebase. Stored as a hash; never logged.
        _admin_key = os.environ.get("FORGEOS_ADMIN_API_KEY", "").strip()
        self._admin_key_hash = hashlib.sha256(_admin_key.encode()).hexdigest() if _admin_key else None
        # Signed session tokens for local email+password login.
        self._session_secret = _load_session_secret()
        self._session_ttl = int(os.environ.get("FORGEOS_SESSION_TTL_SECONDS", str(12 * 3600)))

        if HAS_FIREBASE:
            try:
                # Initialize Firebase Admin SDK (uses GOOGLE_APPLICATION_CREDENTIALS env var)
                if not firebase_admin._apps:
                    firebase_admin.initialize_app()
                self._firebase_initialized = True
                logger.info("Firebase Auth initialized")
            except Exception as e:
                logger.warning("Firebase Auth not available: %s", e)

    def verify_jwt(self, token: str) -> AuthUser | None:
        """Verify a Firebase JWT token and return the authenticated user."""
        if not self._firebase_initialized:
            logger.warning("Firebase not initialized — JWT verification unavailable")
            return None

        try:
            decoded = firebase_auth.verify_id_token(token)
            uid = decoded["uid"]
            email = decoded.get("email", "")

            # Look up tenant and role from database
            if self._db and self._db.is_connected:
                with self._db.admin() as conn:
                    row = conn.execute_one(
                        "SELECT tenant_id, role FROM tenant_users WHERE firebase_uid = %s",
                        (uid,),
                    )
                    if row:
                        return AuthUser(
                            user_id=uid,
                            email=email,
                            tenant_id=row["tenant_id"],
                            role=row["role"],
                            name=decoded.get("name", ""),
                        )

            # Fallback: check custom claims
            tenant_id = decoded.get("tenant_id", "")
            role = decoded.get("role", UserRole.VIEWER)
            if tenant_id:
                return AuthUser(
                    user_id=uid, email=email, tenant_id=tenant_id,
                    role=role, name=decoded.get("name", ""),
                )

            return None

        except Exception as e:
            logger.warning("JWT verification failed: %s", e)
            return None

    def mint_token(self, user: AuthUser, ttl_seconds: int | None = None) -> str:
        """Mint a stateless HMAC-signed session token for ``user``."""
        ttl = ttl_seconds if ttl_seconds is not None else self._session_ttl
        payload = {
            "user_id": user.user_id, "email": user.email, "tenant_id": user.tenant_id,
            "role": user.role, "name": user.name, "exp": int(time.time()) + ttl,
        }
        body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).rstrip(b"=").decode()
        signed = f"v1.{body}"
        sig = base64.urlsafe_b64encode(
            hmac.new(self._session_secret, signed.encode("utf-8"), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        return f"{signed}.{sig}"

    def verify_token(self, token: str) -> AuthUser | None:
        """Verify a signed session token. None on bad sig / expiry / malformed."""
        try:
            parts = token.split(".")
            if len(parts) != 3 or parts[0] != "v1":
                return None
            signed = f"{parts[0]}.{parts[1]}"
            expected = base64.urlsafe_b64encode(
                hmac.new(self._session_secret, signed.encode("utf-8"), hashlib.sha256).digest()
            ).rstrip(b"=").decode()
            if not hmac.compare_digest(parts[2], expected):
                return None
            pad = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            return AuthUser(
                user_id=payload["user_id"], email=payload.get("email", ""),
                tenant_id=payload.get("tenant_id", ""),
                role=payload.get("role", UserRole.VIEWER), name=payload.get("name", ""),
            )
        except Exception:
            return None

    def verify_password(self, email: str, password: str) -> AuthUser | None:
        """Validate local email+password against tenant_users → AuthUser.

        ``tenant_users`` has no RLS policy, so it's queried via ``db.admin()``
        (cross-tenant) with an explicit tenant_id filter."""
        if not self._db or not getattr(self._db, "is_connected", False) or not email or not password:
            return None
        try:
            with self._db.admin() as conn:
                row = conn.execute_one(
                    "SELECT id, tenant_id, email, role, name, password_hash FROM tenant_users "
                    "WHERE tenant_id = %s AND email = %s",
                    (self._tenant_id, email),
                )
        except Exception:
            logger.warning("verify_password lookup failed", exc_info=True)
            return None
        if not row or not row.get("password_hash"):
            return None
        if not verify_password_hash(password, row["password_hash"]):
            return None
        return AuthUser(
            user_id=str(row["id"]), email=row["email"], tenant_id=row["tenant_id"],
            role=row["role"], name=row.get("name") or "",
        )

    def verify_admin_key(self, api_key: str) -> AuthUser | None:
        """Verify the platform admin API key (FORGEOS_ADMIN_API_KEY).

        Returns an ``admin`` principal bound to the boot tenant. Constant-time
        compared; no DB required (the key lives only in env/Secret Manager).
        """
        if not self._admin_key_hash:
            return None
        candidate = hashlib.sha256(api_key.encode()).hexdigest()
        if hmac.compare_digest(candidate, self._admin_key_hash):
            return AuthUser(
                user_id="admin",
                email="admin@platform",
                tenant_id=self._tenant_id,
                role=UserRole.ADMIN,
                name="Platform Admin",
            )
        return None

    def verify_api_key(self, api_key: str) -> AuthUser | None:
        """Verify an API key and return a system user for the tenant.

        Uses constant-time comparison (hmac.compare_digest) to prevent
        timing attacks.  The underlying hash is still SHA-256 for backwards
        compatibility with existing stored hashes.  A future migration should
        move to bcrypt/argon2 with per-key salts.
        """
        if not self._db or not self._db.is_connected:
            return None

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT id, name, api_key_hash FROM tenants "
                "WHERE status = 'active' AND api_key_hash = %s",
                (key_hash,),
            )
            if row and hmac.compare_digest(key_hash, row["api_key_hash"]):
                return AuthUser(
                    user_id=f"api-{row['id']}",
                    email=f"api@{row['id']}",
                    tenant_id=row["id"],
                    role=UserRole.ADMIN,
                    name=f"{row['name']} API",
                )

        return None

    def authenticate(self, request) -> AuthUser | None:
        """Authenticate a Flask request via JWT or API key.

        Enforces per-IP rate limiting: after 10 failed attempts in 60s
        the IP is blocked for 5 minutes.
        """
        ip = getattr(request, "remote_addr", None) or "unknown"

        # Check rate limit before attempting auth
        if not _check_auth_rate_limit(ip):
            logger.warning("Auth blocked for IP %s (rate limited)", ip)
            return None

        auth_header = request.headers.get("Authorization", "")

        # Bearer token — a signed local session token first, then Firebase JWT.
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user = self.verify_token(token) or self.verify_jwt(token)
            if not user:
                _record_auth_failure(ip)
            return user

        # API key — try the platform admin key first, then per-tenant keys.
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            user = self.verify_admin_key(api_key) or self.verify_api_key(api_key)
            if not user:
                _record_auth_failure(ip)
            return user

        return None


def require_auth(auth_manager: AuthManager):
    """Flask decorator that requires authentication."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import request, jsonify, g

            user = auth_manager.authenticate(request)
            if not user:
                return jsonify({"error": "Authentication required"}), 401

            g.user = user
            g.tenant_id = user.tenant_id
            return f(*args, **kwargs)
        return wrapper
    return decorator


def require_role(*roles: str):
    """Flask decorator that requires a specific role."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import g, jsonify

            user = getattr(g, "user", None)
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user.role not in roles:
                return jsonify({"error": f"Role {user.role} not authorized"}), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new API key."""
    return f"fos_{os.urandom(32).hex()}"
