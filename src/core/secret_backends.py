"""Encrypted Postgres backend for :class:`~src.core.secrets.SecretsManager`.

Stores secret values encrypted at rest with app-level Fernet (authenticated
AES) keyed from the ``FORGEOS_CRED_ENC_KEY`` environment variable. It slots
*under* ``SecretsManager`` (consulted between the cache and GCP Secret Manager),
so the same store serves both write-only credential injection (e.g. GitHub PATs)
AND ``secret:<name>`` MCP env resolution (e.g. per-user JIRA tokens) — and it
works locally, where GCP Secret Manager is unavailable.

Key management:
  * Production: inject ``FORGEOS_CRED_ENC_KEY`` (a ``Fernet.generate_key()``
    value, or a comma-separated list for rotation — first is primary) from a
    real secret store into the process env.
  * Local dev: if unset, a STABLE key is derived from a fixed salt so stored
    rows survive restarts, with a loud warning. Never random-at-boot (that
    would make existing rows undecryptable).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, MultiFernet, InvalidToken
    HAS_FERNET = True
except ImportError:  # pragma: no cover - exercised only when cryptography is missing
    HAS_FERNET = False

    class InvalidToken(Exception):  # type: ignore[no-redef]
        pass


_ENC_KEY_ENV = "FORGEOS_CRED_ENC_KEY"
# Fixed salt → a deterministic dev key. Insecure by design; dev-only.
_DEV_KEY_SALT = b"forgeos-local-dev-credential-key-v1"


def _load_fernet() -> "MultiFernet | None":
    """Build a MultiFernet from env, or a stable dev key as a last resort."""
    if not HAS_FERNET:
        logger.warning(
            "cryptography not installed — PostgresSecretBackend disabled. "
            "Add 'cryptography' to your environment to enable it."
        )
        return None

    raw = os.environ.get(_ENC_KEY_ENV, "").strip()
    keys = []
    if raw:
        for k in (part.strip() for part in raw.split(",")):
            if not k:
                continue
            try:
                keys.append(Fernet(k.encode("utf-8")))
            except Exception:
                logger.error("Invalid Fernet key in %s — skipping one entry", _ENC_KEY_ENV)
    if not keys:
        dev_key = base64.urlsafe_b64encode(hashlib.sha256(_DEV_KEY_SALT).digest())
        keys.append(Fernet(dev_key))
        logger.warning(
            "%s not set (or invalid) — using an INSECURE derived dev key for "
            "credential encryption. Set %s=$(python -c \"from cryptography.fernet "
            "import Fernet;print(Fernet.generate_key().decode())\") in any real "
            "deployment.",
            _ENC_KEY_ENV, _ENC_KEY_ENV,
        )
    return MultiFernet(keys)


class PostgresSecretBackend:
    """Encrypted name→value secret store in Postgres, scoped by tenant via RLS.

    Mirrors the minimal surface ``SecretsManager`` needs: ``get(name)`` and
    ``put(name, value, ...)``. All access goes through ``db.tenant()`` so the
    ``user_credentials`` RLS policy applies.
    """

    def __init__(self, db_client: Any, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id
        self._fernet = _load_fernet()

    @property
    def available(self) -> bool:
        return self._fernet is not None and bool(getattr(self._db, "is_connected", False))

    def get(self, name: str) -> str | None:
        """Return the decrypted secret value, or None if absent/undecryptable."""
        if not self.available:
            return None
        try:
            with self._db.tenant(self._tenant_id) as conn:
                row = conn.execute_one(
                    "SELECT enc_value FROM user_credentials "
                    "WHERE tenant_id = %s AND secret_name = %s",
                    (self._tenant_id, name),
                )
            if not row:
                return None
            enc = row["enc_value"]
            if isinstance(enc, memoryview):
                enc = enc.tobytes()
            return self._fernet.decrypt(bytes(enc)).decode("utf-8")
        except InvalidToken:
            logger.error(
                "Could not decrypt credential '%s' — encryption key mismatch "
                "(was %s rotated/changed?)", name, _ENC_KEY_ENV,
            )
            return None
        except Exception:
            logger.exception("PostgresSecretBackend.get failed for '%s'", name)
            return None

    def put(self, name: str, value: str, *, user_id: str = "default", kind: str = "generic") -> bool:
        """Encrypt and upsert a secret. Returns True on success."""
        if not self.available:
            return False
        try:
            enc = self._fernet.encrypt(value.encode("utf-8"))
            with self._db.tenant(self._tenant_id) as conn:
                conn.execute(
                    "INSERT INTO user_credentials "
                    "(tenant_id, user_id, kind, secret_name, enc_value, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, NOW()) "
                    "ON CONFLICT (tenant_id, secret_name) DO UPDATE SET "
                    "enc_value = EXCLUDED.enc_value, kind = EXCLUDED.kind, "
                    "user_id = EXCLUDED.user_id, updated_at = NOW()",
                    (self._tenant_id, user_id, kind, name, enc),
                )
                conn.commit()
            return True
        except Exception:
            logger.exception("PostgresSecretBackend.put failed for '%s'", name)
            return False
