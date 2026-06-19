# Helios OS MCP Server — moved

The Helios OS MCP server (formerly `tools/forgeos-mcp-server.py` and the
`src/forgeos_mcp` package) now lives in its own repository:

**→ https://github.com/antonibergas-hue/forgeos-mcp**

That repo also holds the platform tool-execution integration layer (formerly
`src/mcp/`, now `forgeos_mcp.integration`).

## Run

```bash
pip install -e ../forgeos-mcp        # or from the published package
python3 -m forgeos_mcp                          # stdio (Claude Code, Cursor)
python3 -m forgeos_mcp --transport sse          # SSE (web clients)
python3 -m forgeos_mcp --transport streamable-http --port 8000
# console script: forgeos-mcp
```

Point it at a running Helios OS API first, e.g.:

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

This repo's `.mcp.json` registers it for Claude Code as `forgeos`
(`python3 -m forgeos_mcp`). Environment variables (`FORGEOS_URL`,
`FORGEOS_API_KEY`, `FORGEOS_USER`), the tool catalogue, and tests are documented
in the `forgeos-mcp` repo's README.
