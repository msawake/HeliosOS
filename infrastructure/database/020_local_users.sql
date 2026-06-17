-- ============================================================================
-- ForgeOS Local Users (email + password)
-- Migration 020: give tenant_users local password credentials so the dashboard
-- has real per-user login (admin/operator/viewer) instead of only the shared
-- dev password. Firebase remains supported (firebase_uid is just now optional).
--
-- Additive + forward-only:
--   * password_hash : pbkdf2 hash (NULL for Firebase-only rows / no password)
--   * name          : display name (AuthUser.name had nowhere to persist)
--   * firebase_uid  : now NULLABLE (local users have none). The existing
--                     UNIQUE(firebase_uid) from 001 still guards real uids —
--                     Postgres treats NULLs as distinct, so many NULLs are OK.
--   * UNIQUE(tenant_id, email) : the login lookup key + dedupe guard.
--
-- PRE-FLIGHT: if a tenant already has duplicate emails the UNIQUE add will
-- fail. Check first:
--   SELECT tenant_id, email, count(*) FROM tenant_users
--   GROUP BY 1,2 HAVING count(*) > 1;
-- ============================================================================

ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE tenant_users ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE tenant_users ALTER COLUMN firebase_uid DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tenant_users_tenant_email_uniq'
    ) THEN
        ALTER TABLE tenant_users
            ADD CONSTRAINT tenant_users_tenant_email_uniq UNIQUE (tenant_id, email);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_tenant_users_email
    ON tenant_users(tenant_id, email);
