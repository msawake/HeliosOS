"""Namespace-admin grants — who may manage a namespace's secrets / MCP creds.

Backs the three-tier secret RBAC: the platform admin (tenant ``admin`` role)
implicitly administers every namespace; this store grants namespace-scoped
authority to non-admins. Tenant-isolated via RLS on the ``namespace_admins``
table (migration 018). Degrades to an empty/no-op store when no database is
wired (in-memory dev), so authorization simply falls back to the tenant admin.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def can_write_secret(
    *,
    role: str,
    scope: str,
    namespace: str | None,
    is_namespace_admin: bool,
    admin_role: str = "admin",
) -> bool:
    """Pure RBAC decision for writing/deleting a scoped secret.

    platform  → tenant admin only.
    namespace → tenant admin, or an explicit admin of that namespace.
    user      → always (the caller manages their own user-scoped secrets).
    """
    if scope == "platform":
        return role == admin_role
    if scope == "namespace":
        return role == admin_role or (bool(namespace) and is_namespace_admin)
    if scope == "user":
        return True
    return False


class NamespaceAdminStore:
    """Postgres-backed grants of namespace-admin authority."""

    def __init__(self, db_client: Any = None, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id

    @property
    def available(self) -> bool:
        return bool(getattr(self._db, "is_connected", False))

    def is_admin(self, user_id: str, namespace: str) -> bool:
        """True if ``user_id`` holds an explicit admin grant for ``namespace``."""
        if not self.available or not user_id or not namespace:
            return False
        try:
            with self._db.tenant(self._tenant_id) as conn:
                row = conn.execute_one(
                    "SELECT 1 FROM namespace_admins "
                    "WHERE tenant_id = %s AND namespace = %s AND user_id = %s",
                    (self._tenant_id, namespace, user_id),
                )
            return bool(row)
        except Exception:
            logger.exception("NamespaceAdminStore.is_admin failed (%s/%s)", namespace, user_id)
            return False

    def list_for_namespace(self, namespace: str) -> list[str]:
        """User ids holding an admin grant for ``namespace``."""
        if not self.available:
            return []
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rows = conn.execute(
                    "SELECT user_id FROM namespace_admins "
                    "WHERE tenant_id = %s AND namespace = %s ORDER BY user_id",
                    (self._tenant_id, namespace),
                )
            return [r["user_id"] for r in (rows or [])]
        except Exception:
            logger.exception("NamespaceAdminStore.list_for_namespace failed (%s)", namespace)
            return []

    def namespaces_for_user(self, user_id: str) -> list[str]:
        """Namespaces ``user_id`` administers (for list-scope visibility)."""
        if not self.available or not user_id:
            return []
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rows = conn.execute(
                    "SELECT namespace FROM namespace_admins "
                    "WHERE tenant_id = %s AND user_id = %s ORDER BY namespace",
                    (self._tenant_id, user_id),
                )
            return [r["namespace"] for r in (rows or [])]
        except Exception:
            logger.exception("NamespaceAdminStore.namespaces_for_user failed (%s)", user_id)
            return []

    def grant(self, namespace: str, user_id: str) -> bool:
        """Grant ``user_id`` admin authority over ``namespace``. Idempotent."""
        if not self.available:
            return False
        try:
            with self._db.tenant(self._tenant_id) as conn:
                conn.execute(
                    "INSERT INTO namespace_admins (tenant_id, namespace, user_id) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (self._tenant_id, namespace, user_id),
                )
                conn.commit()
            return True
        except Exception:
            logger.exception("NamespaceAdminStore.grant failed (%s/%s)", namespace, user_id)
            return False

    def revoke(self, namespace: str, user_id: str) -> bool:
        """Revoke ``user_id``'s admin authority over ``namespace``. Idempotent."""
        if not self.available:
            return False
        try:
            with self._db.tenant(self._tenant_id) as conn:
                conn.execute(
                    "DELETE FROM namespace_admins "
                    "WHERE tenant_id = %s AND namespace = %s AND user_id = %s",
                    (self._tenant_id, namespace, user_id),
                )
                conn.commit()
            return True
        except Exception:
            logger.exception("NamespaceAdminStore.revoke failed (%s/%s)", namespace, user_id)
            return False
