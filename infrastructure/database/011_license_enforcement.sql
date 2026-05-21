-- License enforcement: subscription tracking on tenants table
-- Adds columns for Stripe subscription state, grace periods, and plan tracking.

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'trial',
    ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'active'
        CHECK (subscription_status IN ('active', 'past_due', 'cancelled', 'trialing', 'paused')),
    ADD COLUMN IF NOT EXISTS subscription_ends_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS grace_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tenants_subscription_status
    ON tenants(subscription_status) WHERE subscription_status != 'active';
