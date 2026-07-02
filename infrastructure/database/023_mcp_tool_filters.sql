-- ============================================================================
-- ForgeOS / Helios OS — per-server MCP tool allow/deny
-- Migration 023: extend client_mcp_configs with allowed_tools + disallowed_tools
-- so a registered MCP server can be scoped to a subset of its tools (LiteLLM-
-- style allow/deny). Both columns are nullable JSONB; existing rows keep working
-- (NULL allowed_tools = allow every tool; NULL/[] disallowed_tools = deny none).
--
-- Tool names here are the BARE upstream names (e.g. "getJiraIssue"), matched
-- before the `mcp__<server>__` prefix is applied when advertising to the LLM.
-- Enforced at BOTH funnels: advertising (ClientMCPManager.get_all_client_tools)
-- and execution (ToolExecutor._execute_mcp_tool) — advertising-only filtering is
-- bypassable because an LLM can name an unadvertised tool directly.
-- ============================================================================

ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS allowed_tools    JSONB;   -- NULL = allow all
ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS disallowed_tools JSONB;   -- NULL/[] = deny none
