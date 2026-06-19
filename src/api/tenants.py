"""
Tenant management API for Helios OS SaaS.

Handles tenant CRUD, onboarding, configuration, and lifecycle.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from src.api.auth import generate_api_key, hash_api_key

logger = logging.getLogger(__name__)


class TenantManager:
    """Manages tenant lifecycle: create, configure, boot, suspend."""

    def __init__(self, db_client=None):
        self._db = db_client

    def create_tenant(
        self,
        name: str,
        company_type: str = "leadforge",
        plan: str = "starter",
        config: dict | None = None,
    ) -> dict:
        """Create a new tenant and return tenant info + API key."""
        tenant_id = str(uuid.uuid4())[:12]
        api_key = generate_api_key()
        api_key_hash = hash_api_key(api_key)

        tenant = {
            "id": tenant_id,
            "name": name,
            "company_type": company_type,
            "plan": plan,
            "status": "active",
            "config": config or {},
            "api_key": api_key,  # Only returned on creation
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if self._db and self._db.is_connected:
            import json
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO tenants (id, name, company_type, plan, config, api_key_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (tenant_id, name, company_type, plan,
                     json.dumps(config or {}), api_key_hash),
                )
                conn.commit()
            logger.info("Tenant created: %s (%s) [%s]", name, tenant_id, plan)
        else:
            logger.info("Tenant created (in-memory): %s (%s)", name, tenant_id)

        return tenant

    def get_tenant(self, tenant_id: str) -> dict | None:
        """Get tenant info."""
        if not self._db or not self._db.is_connected:
            return None

        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT id, name, plan, status, company_type, config, created_at "
                "FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            return dict(row) if row else None

    def list_tenants(self, status: str = "active") -> list[dict]:
        """List all tenants."""
        if not self._db or not self._db.is_connected:
            return []

        with self._db.admin() as conn:
            rows = conn.execute(
                "SELECT id, name, plan, status, company_type, created_at "
                "FROM tenants WHERE status = %s ORDER BY created_at DESC",
                (status,),
            )
            return [dict(r) for r in rows] if rows else []

    def update_plan(self, tenant_id: str, plan: str) -> bool:
        """Update a tenant's plan."""
        if not self._db or not self._db.is_connected:
            return False

        with self._db.admin() as conn:
            result = conn.execute(
                "UPDATE tenants SET plan = %s, updated_at = NOW() WHERE id = %s",
                (plan, tenant_id),
            )
            conn.commit()
            return bool(result)

    def suspend_tenant(self, tenant_id: str) -> bool:
        """Suspend a tenant (stop all agent activity)."""
        if not self._db or not self._db.is_connected:
            return False

        with self._db.admin() as conn:
            result = conn.execute(
                "UPDATE tenants SET status = 'suspended', updated_at = NOW() WHERE id = %s",
                (tenant_id,),
            )
            conn.commit()
            logger.warning("Tenant suspended: %s", tenant_id)
            return bool(result)

    def add_user(
        self,
        tenant_id: str,
        firebase_uid: str,
        email: str,
        role: str = "viewer",
    ) -> str:
        """Add a user to a tenant."""
        user_id = str(uuid.uuid4())

        if self._db and self._db.is_connected:
            with self._db.admin() as conn:
                conn.execute(
                    "INSERT INTO tenant_users (id, tenant_id, firebase_uid, email, role) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (user_id, tenant_id, firebase_uid, email, role),
                )
                conn.commit()

        return user_id

    def seed_tenant(self, tenant_id: str, company_type: str = "leadforge") -> None:
        """Seed a tenant's knowledge base with company-specific policies."""
        import importlib
        from src.mcp.custom_tools import CompanySystem

        system = CompanySystem(company_id=company_type, db_client=self._db)
        knowledge_mod = importlib.import_module(f"src.companies.{company_type}.knowledge")
        knowledge_mod.seed_knowledge_base(system.knowledge)
        logger.info("Tenant %s seeded with %s knowledge base", tenant_id, company_type)
