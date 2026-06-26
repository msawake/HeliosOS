"""Idempotently ensure a platform admin login exists.

Re-ports the bootstrap-admin seeding that was lost in the FastAPI->Django
migration (it used to live in src/dashboard/fastapi_app.py). Safe to run
repeatedly — on a fresh deploy it creates the admin; on an existing one it
resets the password to the configured value.

Two surfaces are seeded:
  1. A Django superuser  -> lets you log into Django admin at /admin/.
  2. A platform tenant_users admin row (best-effort) -> lets the dashboard /
     /api/auth/login email+password login work.

Config (env, overridable by flags):
  FORGEOS_BOOTSTRAP_ADMIN_EMAIL     default "admin@forgeos.local"
  FORGEOS_BOOTSTRAP_ADMIN_PASSWORD  required (no default — fail loudly)
  FORGEOS_TENANT_ID                 default "leadforge" (tenant for #2)

Run as a one-off Cloud Run job on the platform-api image (see the
`create-admin` job in .github/workflows/pulumi-infra.yml):
    python manage.py ensure_admin
"""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Idempotently ensure a Django superuser (and platform admin) login exists."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=None, help="Admin email/username.")
        parser.add_argument(
            "--password",
            default=None,
            help="Admin password (prefer FORGEOS_BOOTSTRAP_ADMIN_PASSWORD env).",
        )
        parser.add_argument(
            "--tenant-id",
            default=None,
            help="Tenant id for the platform tenant_users row.",
        )

    def handle(self, *args, **opts):
        email = (
            opts.get("email")
            or os.environ.get("FORGEOS_BOOTSTRAP_ADMIN_EMAIL")
            or "admin@forgeos.local"
        ).strip()
        password = opts.get("password") or os.environ.get("FORGEOS_BOOTSTRAP_ADMIN_PASSWORD")
        tenant_id = (
            opts.get("tenant_id") or os.environ.get("FORGEOS_TENANT_ID") or "leadforge"
        ).strip()

        if not password:
            raise CommandError(
                "No admin password provided. Set FORGEOS_BOOTSTRAP_ADMIN_PASSWORD "
                "(or pass --password)."
            )

        # Guard against the SQLite fallback (settings.py uses it when DATABASE_URL
        # is unset) — seeding an admin into a throwaway DB is never what we want.
        engine = connection.settings_dict.get("ENGINE", "")
        if "sqlite" in engine:
            raise CommandError(
                "Refusing to seed admin into a SQLite database — DATABASE_URL is "
                "not set. Wire the Postgres DATABASE_URL and retry."
            )

        # --- 1. Django superuser (Django admin /admin/) ----------------------
        from django.contrib.auth.models import User

        user, created = User.objects.get_or_create(
            username=email, defaults={"email": email}
        )
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} Django superuser '{email}'."
            )
        )

        # --- 2. Platform admin (dashboard / API login) — best-effort ---------
        try:
            self._seed_tenant_admin(email, password, tenant_id)
        except Exception as exc:  # never fail the superuser path
            self.stderr.write(
                self.style.WARNING(
                    f"Platform tenant_users admin seed skipped (non-fatal): {exc}"
                )
            )

    def _seed_tenant_admin(self, email: str, password: str, tenant_id: str) -> None:
        """Upsert a tenant_users admin row so dashboard email+password works.

        tenant_users has no RLS policy, so a plain connection write is fine.
        Requires migration 020 (password_hash + UNIQUE(tenant_id, email)).
        """
        from src.api.auth import hash_password

        password_hash = hash_password(password)
        with connection.cursor() as cur:
            # The tenant must exist (FK). If it doesn't, the INSERT raises and
            # the caller swallows it — boot seeding creates the tenant normally.
            cur.execute(
                """
                INSERT INTO tenant_users (tenant_id, email, role, name, password_hash)
                VALUES (%s, %s, 'admin', 'Bootstrap Admin', %s)
                ON CONFLICT (tenant_id, email)
                DO UPDATE SET role = 'admin', password_hash = EXCLUDED.password_hash
                """,
                (tenant_id, email, password_hash),
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded platform admin '{email}' on tenant '{tenant_id}'."
            )
        )
