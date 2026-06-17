# Helios OS — AI Coding Agent Context

Helios OS is an operating system for AI agents. It governs what agents can do — budgets, permissions, tool access, human-in-the-loop — across 9 framework adapters (Helios OS, CrewAI, ADK, LangChain, OpenClaw, Sandbox, Anthropic SDK, Anthropic Managed, OpenAI Agents). The kernel enforces policy. The runtime is the agent-side interface. Agents don't change their code — governance wraps around them.

## Quick Start

```bash
pip install -e ".[dev]"
PYTHONPATH=. python3 -m pytest              # 1236 tests, 78 files
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

## Key Directories

```
src/platform/kernel/       BSL 1.1 — permission, budget, policy, capability, syscall (5 files)
src/platform/kernel_stubs/ Apache 2.0 — permissive stubs for Community Edition
src/forgeos_sdk/           SDK: runtime.py (BSL), kernel.py (BSL), agent.py, client.py, manifest.py (Apache)
src/platform/              Registry, executor, scheduler, event bus, LLM router, audit, A2A, process table
src/dashboard/             FastAPI backend (~2500 lines, 70+ endpoints)
stacks/                    9 framework adapters (each wraps tools with kernel gate)
dashboard/                 Next.js 15 frontend (22 pages)
mission-control/           Fleet ops dashboard (React + FastAPI)
examples/                  8 gold-standard agents with raw vs governed comparisons
docs/                      44 markdown files — architecture, guides, reference
tests/                     78 test files, 1236 passing
deploy/k8s/                Kubernetes manifests with Kustomize overlays
infrastructure/docker/     Dockerfiles for API, dashboard, Mission Control
```

## Architecture (3 layers)

1. **Kernel** — 6 subsystems: admission, permissions, budgets, policies, data boundaries, capabilities
2. **Platform** — registry, executor, scheduler, event bus, LLM router, agentic loop, A2A, audit
3. **Adapters** — 9 framework adapters that wrap tool calls with kernel governance

## Runtime API (27 methods)

```python
from forgeos_sdk.runtime import runtime

runtime.check_tool("send_email")           # permission gate
runtime.budget()                            # check remaining budget
runtime.reserve(0.05) / commit() / release() # two-phase budget
runtime.checkpoint({"step": 3})             # crash recovery
runtime.pending_signals()                   # cooperative shutdown
runtime.request_capability(target, verb, ttl) # temporary access token
runtime.ask_human(ns, name, question)       # HITL escalation
runtime.audit(event, details)               # hash-chained audit trail
```

## Common Tasks

### Deploy an agent
```bash
forgeos deploy examples/sre-gcp-auditor/manifest.yaml
```

### Run an example agent
```bash
PYTHONPATH=. python3 examples/sre-gcp-auditor/agent.py
PYTHONPATH=. python3 examples/content-ops/agent.py
PYTHONPATH=. python3 examples/sre-command-center/agent.py
```

### Run Mission Control
```bash
cd mission-control && make dev
```

### Run tests
```bash
PYTHONPATH=. python3 -m pytest                           # all
PYTHONPATH=. python3 -m pytest tests/test_kernel.py      # single file
PYTHONPATH=. python3 -m pytest -k "test_budget"          # by pattern
```

## Conventions

- `PYTHONPATH=.` required (stacks/ is a top-level package alongside src/)
- Use `python3`, not `python` (macOS has no python symlink)
- `asyncio_mode = "auto"` in pyproject.toml — async tests don't need `@pytest.mark.asyncio`
- Manifests use `apiVersion: forgeos/v1` (simple) or `agentos/v1` (k8s-style)
- BSL 1.1 on kernel + runtime. Apache 2.0 on everything else.

## License

Dual-licensed: BSL 1.1 (kernel, runtime) + Apache 2.0 (adapters, SDK client libs, examples, docs).
Community Edition: full Apache 2.0 with permissive stubs.
