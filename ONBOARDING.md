# ForgeOS — Onboarding Guide

> The agentic harness: deploy, orchestrate, and govern AI agents across 9 framework adapters with a kernel, syscall pipeline, runtime SDK, and inter-agent protocols.

## What is this?

ForgeOS is the **harness** (the operating system); agents are the **processes** that run inside it. The framework provides scheduling, tool execution, LLM routing, persistence, and monitoring — agents define what work gets done, declared as k8s-style YAML manifests and governed by the kernel on every tool call, budget spend, and agent-to-agent call.

**Repo:** [makingscience-awake/forgeos](https://github.com/makingscience-awake/forgeos) · **CLI:** [antonibergas-hue/forgeos-cli](https://github.com/antonibergas-hue/forgeos-cli)

---

## Quick Start

### Prerequisites

| Tool | Needed for | Install |
|------|-----------|---------|
| Docker | the one-command local stack | [docker.com](https://docker.com) |
| Rust toolchain | building the CLI | [rustup.rs](https://rustup.rs) |
| Python 3.11+ | running on the host / tests | `brew install python3` |

### 1. Boot the stack (Docker — zero config)

```bash
git clone https://github.com/makingscience-awake/forgeos.git
cd forgeos
docker compose up
```

This boots PostgreSQL (pgvector), Redis, and the platform API on http://localhost:5000. API auth is disabled for local testing, and without API keys all LLM calls return **simulated responses** — you can exercise the full deploy/invoke/governance loop for free.

For real model responses:

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY (and/or OPENAI_API_KEY)
docker compose up
```

### 2. Install the CLI

The `forgeos` CLI is a single static Rust binary, maintained in its own repo:

```bash
git clone https://github.com/antonibergas-hue/forgeos-cli.git
cd forgeos-cli
cargo build --release
sudo cp target/release/forgeos /usr/local/bin/
```

(Prefer Python? `pip install -e .` in the main repo installs an equivalent `forgeos` CLI from the SDK — use `FORGEOS_API_URL` instead of `FORGEOS_REMOTE` below.)

### 3. Deploy your first agent

```bash
export FORGEOS_REMOTE=http://localhost:5000

forgeos health

cat > hello.yaml <<'EOF'
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: hello
  description: "A simple test agent"
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  system_prompt: |
    You are a friendly hello-world agent. Keep replies short.
EOF

forgeos deploy hello.yaml      # prints the agent id, e.g. "Deployed agent: 1c5b3f3d-93f"
forgeos list
forgeos invoke <agent-id> "Hello, what can you do?" --wait
forgeos logs <agent-id>
```

More example manifests (all five lifecycles, multiple stacks) live in `examples/` — try `forgeos deploy examples/forgeos/hello-world.yaml`.

### Verify it works

- [ ] `curl http://localhost:5000/api/health` returns `"status": "ok"`
- [ ] `forgeos health` shows adapters and a database connection
- [ ] `forgeos invoke … --wait` returns a response (simulated without keys)
- [ ] `forgeos logs <agent-id>` shows the `run.started` → `run.completed` trail

---

## Developing on the host

```bash
pip install -e ".[dev]"                 # Python 3.11+

# Boot the platform (dev: no auth, in-memory DB unless DATABASE_URL is set)
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Next.js dashboard (separate terminal) → http://localhost:3000
cd dashboard && npm install && npm run dev

# Tests (~1256 tests) / lint / types
PYTHONPATH=. python3 -m pytest
ruff check src/ tests/
mypy src/
```

For persistence while developing on the host, run just the data services from Docker:

```bash
docker compose up -d postgres redis
echo "DATABASE_URL=postgresql://leadforge_admin:forgeoslocal@localhost:5433/leadforge" >> .env
echo "REDIS_URL=redis://localhost:6379" >> .env
```

---

## Repo layout

| Path | Purpose |
|------|---------|
| `src/platform/` | Kernel, syscall pipeline, registry, executor, scheduler, A2A, audit |
| `src/forgeos_sdk/` | Python SDK: manifest schema, client, runtime, CLI |
| `src/core/` | Database (multi-tenant RLS), session store, model clients, legacy hooks |
| `src/mcp/` | MCP server manager + tool executor |
| `stacks/` | The 9 framework adapters (`AgentStackAdapter` ABC in `base.py`) |
| `examples/` | Example agents per stack — governed `agent.py` vs ungoverned `agent_raw.py` |
| `dashboard/` | Next.js 15 + React 19 frontend |
| `docker-compose.yaml` | One-command local stack (Postgres + Redis + API) |
| `infrastructure/`, `deploy/`, `pulumi/` | Docker images, SQL migrations, K8s, IaC |

## Conventions that will bite you if you skip them

- `PYTHONPATH=.` is required for tests and host boot — `stacks/` is a top-level package alongside `src/`.
- Use `python3`, not `python` (macOS ships no `python` symlink).
- Async tests need no `@pytest.mark.asyncio` — `asyncio_mode = "auto"` is set.
- Two admission paths coexist: legacy `src/core/hooks.py` runs by default; set `FORGEOS_SYSCALL_PIPELINE=1` for the new syscall pipeline.
- Graceful degradation everywhere: no API key → simulation, no DB → in-memory, no Redis → in-memory.
- The audit trail is hash-chained — append only, never mutate past records.
- Secrets live in `.env` (gitignored); never commit keys.

## Where to read next

- `README.md` — architecture diagram, kernel subsystems, SDK runtime methods
- `docs/guides/quickstart.md` · `docs/guides/creating-agents.md` · `docs/guides/sdk.md`
- `docs/architecture/overview.md` · `docs/architecture/kernel.md` · `docs/architecture/a2a-protocol.md`
- `docs/reference/agent-manifest.md` — full `agent.yaml` schema
