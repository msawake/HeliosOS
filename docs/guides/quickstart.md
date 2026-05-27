# Quick Start Guide

Get ForgeOS running locally in 5 minutes.

## Prerequisites

- **Python 3.11+** (`python3 --version`)
- **Node.js 18+** (`node --version`) -- for the dashboard
- **Anthropic API key** (or OpenAI) -- get one at https://console.anthropic.com

Optional:
- **Docker** -- for PostgreSQL persistence (otherwise data is in-memory)
- **Redis** -- for distributed rate limiting (falls back to in-memory)

## 1. Install

```bash
cd forgeos/

# Create virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -e ".[dev]"

# Install dashboard dependencies
cd dashboard && npm install && cd ..
```

## 2. Configure

Create a `.env` file in the project root:

```bash
# Required -- at least one LLM provider
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional -- enables OpenAI models (gpt-4o, o3, etc.)
# OPENAI_API_KEY=sk-...

# Optional -- PostgreSQL for persistence (otherwise in-memory)
# DATABASE_URL=postgresql://user:pass@localhost:5433/forgeos

# Optional -- Redis for distributed rate limiting
# REDIS_URL=redis://localhost:6379
```

## 3. Boot the Platform

```bash
# Start the backend API
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

You should see:

```
BOOTING FORGEOS MULTI-STACK PLATFORM
Company: leadforge | Mode: supervised
[Phase 1] Initializing platform subsystems...
  LLM Router: providers=['anthropic']
[Phase 2] Initializing legacy company subsystems...
  Database: IN-MEMORY (set DATABASE_URL for persistence)
[Phase 3] Registering stack adapters...
  Stack registered: forgeos
  Stack registered: crewai
  Stack registered: adk
  Stack registered: openclaw
...
FORGEOS PLATFORM ONLINE
API: http://localhost:5000 (FastAPI)
```

## 4. Start the Dashboard

In a separate terminal:

```bash
cd forgeos//dashboard
npm run dev
```

Open http://localhost:3000 in your browser. You'll see the ForgeOS dashboard with agent management, admin chat, and system monitoring.

## 5. Deploy Your First Agent

Using curl (or the dashboard):

```bash
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-first-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "A simple assistant that answers questions",
    "chat_model": "claude-sonnet-4-5-20250514",
    "system_prompt": "You are a helpful assistant. Answer questions clearly and concisely."
  }' | python3 -m json.tool
```

> **Tip — the `forgeos` CLI.** Instead of curl, you can use the standalone Rust
> CLI, which lives in its own repo: [`antonibergas-hue/forgeos-cli`](https://github.com/antonibergas-hue/forgeos-cli)
> (`cargo build --release`). Then: `forgeos deploy agent.yaml`,
> `forgeos list`, `forgeos describe <id>`, `forgeos invoke <id> "prompt"`
> (fire-and-return; add `--wait` to block), and `forgeos logs <id> --follow`.

## 6. Chat with Your Agent

Use the streaming chat endpoint:

```bash
curl -N http://localhost:5000/api/platform/agents/my-first-agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "What is ForgeOS?"}'
```

Or open the agent in the dashboard and use the chat interface.

## 7. Invoke with Tools

Deploy an agent with MCP tools (if you have MCP servers connected):

```bash
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "file-reader",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "Reads and summarizes files from your filesystem",
    "tools": ["mcp__filesystem__read_file", "mcp__filesystem__list_directory"],
    "chat_model": "claude-sonnet-4-5-20250514"
  }' | python3 -m json.tool
```

---

## Optional: PostgreSQL Persistence

Without a database, all agents, sessions, and audit logs are lost on restart. To add persistence:

```bash
# Start PostgreSQL via Docker
cd infrastructure/docker
bash docker-setup.sh                    # Generates .env with random password
docker compose up -d postgres           # Start only Postgres (port 5433)

# Add DATABASE_URL to project .env
DB_PASS=$(grep DB_PASSWORD infrastructure/docker/.env | cut -d= -f2)
echo "DATABASE_URL=postgresql://leadforge_admin:${DB_PASS}@localhost:5433/leadforge" >> ../../.env
```

Restart the backend. You should see:

```
Database: CONNECTED (PostgreSQL)
Migrations: 5 applied, 0 skipped
```

---

## Common Issues

**`ModuleNotFoundError: No module named 'stacks'`**
- Add `PYTHONPATH=.` before the command. Both `src/` and `stacks/` must be importable from the project root.

**`Port 5000 already in use`**
- Use a different port: `--port 5001`

**`ANTHROPIC_API_KEY not set`**
- Create a `.env` file in the project root (not in `infrastructure/docker/`).

**Dashboard shows "Cannot connect to API"**
- Make sure the backend is running on port 5000. If using a different port, set `FORGEOS_API_URL=http://localhost:PORT` in `dashboard/.env`.

**`MCP connect_all() timed out`**
- MCP server connections have a 30s timeout. Check that the configured MCP packages are installable. Disable slow servers in `src/companies/leadforge/config.yaml`.

---

## Next Steps

- [Creating Agents](creating-agents.md) -- Learn the 5 execution types and 3 ownership types
- [Agent Tools & MCP](agent-tools.md) -- Connect MCP servers and assign tools to agents
- [Architecture Overview](../architecture/overview.md) -- Understand the framework vs agent distinction
- [API Reference](../reference/api-endpoints.md) -- All 61 FastAPI endpoints
