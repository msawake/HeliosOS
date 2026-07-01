-- ============================================================================
-- ForgeOS / Helios OS — MCP HTTP transport support
-- Migration 021: extend client_mcp_configs with transport + url so agents can
-- consume remote MCP servers (streamable-http / SSE) in addition to the
-- existing stdio subprocess model. Adds two nullable columns; existing rows
-- keep working (transport defaults to 'stdio').
--
-- Backward compat:
--   * transport = 'stdio' (default) → connect via subprocess using package/args
--     (unchanged behavior).
--   * transport = 'streamable-http' or 'sse' → connect via HTTP; the `url`
--     column carries the endpoint. `package` becomes optional for these rows
--     (kept NOT NULL but empty-string is accepted); `env_vars` doubles as the
--     HTTP header map for the outbound request.
-- ============================================================================

ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS transport TEXT NOT NULL DEFAULT 'stdio'
        CHECK (transport IN ('stdio', 'streamable-http', 'sse'));

ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS url TEXT;

-- Consistency guard: HTTP transports must carry a URL; stdio must not.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'client_mcp_transport_shape'
    ) THEN
        ALTER TABLE client_mcp_configs
            ADD CONSTRAINT client_mcp_transport_shape CHECK (
                (transport = 'stdio' AND url IS NULL)
             OR (transport IN ('streamable-http', 'sse') AND url IS NOT NULL AND url <> '')
            );
    END IF;
END $$;
