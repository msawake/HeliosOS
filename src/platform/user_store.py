"""Tenant user accounts (local email+password) — CRUD over ``tenant_users``.

Backs the dashboard's user-management UI + local login. Unlike the namespace
stores, ``tenant_users`` has NO RLS policy, so this store queries via
``db.admin()`` (cross-tenant clear) with an explicit ``tenant_id`` filter —
mirroring how ``AuthManager.verify_jwt`` / ``verify_password`` read it.

Degrades to an empty/no-op store when no database is wired (in-memory dev /
auth disabled), matching NamespaceStore/NamespaceAdminStore.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.api.auth import hash_password

logger = logging.getLogger(__name__)


class UserStore:
    def __init__(self, db_client: Any = None, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id

    @property
    def available(self) -> bool:
        return bool(getattr(self._db, "is_connected", False))

    def list_users(self) -> list[dict[str, Any]]:
        """All users for the tenant — never includes password_hash."""
        if not self.available:
            return []
        try:
            with self._db.admin() as conn:
                rows = conn.execute(
                    "SELECT id, email, role, name, created_at, "
                    "(firebase_uid IS NOT NULL) AS is_federated "
                    "FROM tenant_users WHERE tenant_id = %s ORDER BY email",
                    (self._tenant_id,),
                )
            return [
                {"id": str(r["id"]), "email": r["email"], "role": r["role"],
                 "name": r.get("name") or "", "created_at": r.get("created_at"),
                 "is_federated": bool(r.get("is_federated"))}
                for r in (rows or [])
            ]
        except Exception:
            logger.exception("UserStore.list_users failed")
            return []

    def get_by_email(self, email: str) -> dict | None:
        if not self.available or not email:
            return None
        try:
            with self._db.admin() as conn:
                return conn.execute_one(
                    "SELECT id, email, role, name FROM tenant_users "
                    "WHERE tenant_id = %s AND email = %s",
                    (self._tenant_id, email),
                )
        except Exception:
            logger.exception("UserStore.get_by_email failed")
            return None

    def get_by_id(self, user_id: str) -> dict | None:
        if not self.available:
            return None
        try:
            with self._db.admin() as conn:
                return conn.execute_one(
                    "SELECT id, email, role, name FROM tenant_users "
                    "WHERE tenant_id = %s AND id = %s",
                    (self._tenant_id, user_id),
                )
        except Exception:
            logger.exception("UserStore.get_by_id failed")
            return None

    def create_user(self, email: str, password: str, *, role: str = "viewer", name: str = "") -> dict:
        """Create a local user. Raises ValueError on duplicate email / no DB."""
        if not self.available:
            raise ValueError("user store not available")
        uid = str(uuid.uuid4())
        try:
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO tenant_users (id, tenant_id, email, role, name, password_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (uid, self._tenant_id, email, role, name, hash_password(password)),
                )
                conn.commit()
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise ValueError(f"a user with email '{email}' already exists")
            logger.exception("UserStore.create_user failed")
            raise ValueError("could not create user")
        return {"id": uid, "email": email, "role": role, "name": name}

    def set_role(self, user_id: str, role: str) -> bool:
        return self._update(user_id, "role = %s", (role,))

    def set_password(self, user_id: str, password: str) -> bool:
        return self._update(user_id, "password_hash = %s", (hash_password(password),))

    def set_name(self, user_id: str, name: str) -> bool:
        return self._update(user_id, "name = %s", (name,))

    def _update(self, user_id: str, set_clause: str, params: tuple) -> bool:
        if not self.available:
            return False
        try:
            with self._db.admin() as conn:
                conn.execute(
                    f"UPDATE tenant_users SET {set_clause} WHERE tenant_id = %s AND id = %s",
                    (*params, self._tenant_id, user_id),
                )
                conn.commit()
            return True
        except Exception:
            logger.exception("UserStore._update failed (%s)", user_id)
            return False

    def count_admins(self, *, excluding: str | None = None) -> int:
        """Number of admin users in the tenant (optionally excluding one id)."""
        if not self.available:
            return 0
        try:
            sql = "SELECT count(*) AS n FROM tenant_users WHERE tenant_id = %s AND role = 'admin'"
            params: list[Any] = [self._tenant_id]
            if excluding:
                sql += " AND id <> %s"
                params.append(excluding)
            with self._db.admin() as conn:
                row = conn.execute_one(sql, tuple(params))
            return int(row["n"]) if row else 0
        except Exception:
            logger.exception("UserStore.count_admins failed")
            return 0

    def delete_user(self, user_id: str) -> bool:
        if not self.available:
            return False
        try:
            with self._db.admin() as conn:
                conn.execute(
                    "DELETE FROM tenant_users WHERE tenant_id = %s AND id = %s",
                    (self._tenant_id, user_id),
                )
                conn.commit()
            return True
        except Exception:
            logger.exception("UserStore.delete_user failed (%s)", user_id)
            return False
