"""
Authentication and authorization for ForgeOS SaaS.

Supports:
- Firebase Auth (JWT tokens) for dashboard users
- API key auth for programmatic access
- Role-based access control (Admin, Operator, Viewer)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

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

        # Bearer token (JWT)
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user = self.verify_jwt(token)
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
