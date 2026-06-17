-- ============================================================================
-- Helios OS Agent Runs — Migration 012
--
-- Add per-run input/output token split + model name so the dashboard can
-- compute USD cost on read (model price × tokens) rather than storing a
-- precomputed cost that drifts when pricing changes.
-- ============================================================================

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS input_tokens  INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS model         TEXT;
