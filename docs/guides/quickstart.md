# Quick Start Guide

Get Helios OS running locally in a few minutes.

## Prerequisites

- **Python 3.11+** (`python3 --version`)
- **Anthropic API key** (or OpenAI) — get one at https://console.anthropic.com

Optional:

- **Docker** — easiest path (`docker compose up` boots Postgres, Redis, API, worker)
- **Node.js 18+** — only if you want the optional Next.js dashboard UI from a separate repo

## 1. Install (host)

```bash
cd HeliosOS/

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

This installs the platform library, test tools, and the **`forgeos` Python CLI** (`src/forgeos_sdk/cli.py`).

## 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY=sk-ant-...
```

## 3. Boot the platform API

The `--dashboard` flag starts the **HTTP API** (Django ASGI via `forgeos_web`), not the Next.js UI:

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

You should see the platform come online and the API listening on port 5000.

- Health: `curl http://localhost:5000/api/health`
- OpenAPI (when enabled): http://localhost:5000/docs

### Docker alternative (recommended)

```bash
docker compose up
```

Boots Postgres, Redis, API, Celery worker, and beat — no sibling repos required.

## 4. Optional — Next.js dashboard UI

The operator dashboard is **not** in this open-core tree. To use the standalone UI:

```bash
git clone https://github.com/antonibergas-hue/forgeos-dashboard.git ../forgeos-dashboard
cd ../forgeos-dashboard && npm install
echo 'FORGEOS_API_URL=http://localhost:5000' > .env.local
npm run dev
```

Open http://localhost:3000. Or use `docker compose --profile ui up` with `../forgeos-dashboard` checked out.

The integrated dashboard in production lives in the private **heliosos-enterprise** monorepo (`src/heliosos-dashboard/`).

## 5. Deploy your first agent

Using the in-repo Python CLI:

```bash
export FORGEOS_API_URL=http://localhost:5000
forgeos health
forgeos deploy examples/forgeos/hello-world.yaml
forgeos list
```

Or with curl:

```bash
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-first-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "A simple assistant",
    "chat_model": "claude-sonnet-4-6",
    "system_prompt": "You are a helpful assistant."
  }' | python3 -m json.tool
```

### Terminal Mission Control

```bash
forgeos mc fleet
forgeos mc agents
```

`forgeos mc` is the in-repo terminal UI (no separate CLI repo required). An optional Rust CLI exists at [antonibergas-hue/forgeos-cli](https://github.com/antonibergas-hue/forgeos-cli).

## 6. Chat with your agent

```bash
curl -N http://localhost:5000/api/platform/agents/my-first-agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Helios OS?"}'
```

---

## Optional: PostgreSQL persistence

```bash
docker compose up -d postgres
echo "DATABASE_URL=postgresql://leadforge_admin:forgeoslocal@localhost:5433/leadforge" >> .env
```

Restart the API. Migrations run on boot.

---

## Common issues

**`ModuleNotFoundError: No module named 'stacks'`** — prefix commands with `PYTHONPATH=.`

**`Port 5000 already in use`** — use `--port 5001`

**`ANTHROPIC_API_KEY not set`** — create `.env` in the repo root

**Dashboard cannot reach API** — set `FORGEOS_API_URL=http://localhost:5000` in the dashboard's `.env.local`

---

## Next steps

- [Defining Agents](defining-agents.md) — worked example with `examples/jira-greeter-v2`
- [Runtime & Deployment](runtime-and-deployment.md)
- [Example Agents](example-agents.md)
- [Architecture Overview](../architecture/overview.md)