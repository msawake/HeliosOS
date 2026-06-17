-- 020_continuation_gateway_cols.sql
--
-- Backfill the per-agent LLM-gateway columns on `continuations` for databases
-- whose table was created before commit f1a539a added them. Migration 013 was
-- edited to include these columns, but its `CREATE TABLE IF NOT EXISTS` does not
-- alter an already-existing table, so a DB migrated before that edit is left
-- without them — and the per-turn runtime's INSERT (provider/chat_model/
-- endpoint/api_key_ref) fails with `column "endpoint" ... does not exist`,
-- breaking agent invocation. Add them explicitly (idempotent).

ALTER TABLE continuations ADD COLUMN IF NOT EXISTS provider    TEXT NOT NULL DEFAULT 'anthropic';
ALTER TABLE continuations ADD COLUMN IF NOT EXISTS chat_model  TEXT NOT NULL DEFAULT '';
ALTER TABLE continuations ADD COLUMN IF NOT EXISTS endpoint    TEXT;
ALTER TABLE continuations ADD COLUMN IF NOT EXISTS api_key_ref TEXT;
