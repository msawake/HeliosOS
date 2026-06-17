# Helios OS — Copilot Context

Helios OS is an agentic harness — an operating system for AI agents. It governs tool access, budgets, permissions, and human-in-the-loop across 9 framework adapters.

## Key Facts
- Python 3.11+, FastAPI backend, Next.js dashboard
- 9 adapters: Helios OS, CrewAI, ADK, LangChain, OpenClaw, Sandbox, Anthropic SDK, Anthropic Managed, OpenAI Agents
- Kernel (BSL 1.1): `src/platform/kernel/` — permissions, budgets, policies, capabilities, syscall pipeline
- Runtime (BSL 1.1): `src/forgeos_sdk/runtime.py` — 27-method agent-side API
- Adapters + SDK client + examples: Apache 2.0
- Tests: `PYTHONPATH=. python3 -m pytest` (1236 passing)
- Always use `python3` and `PYTHONPATH=.`

## Architecture
- `src/platform/kernel/` — 5 kernel files (facade, syscall, capabilities, process, checkpoint)
- `src/forgeos_sdk/` — SDK (runtime, kernel accessor, agent builder, manifest, CLI)
- `src/platform/` — registry, executor, scheduler, event bus, LLM router, audit, A2A
- `stacks/` — 9 framework adapters
- `examples/` — 8 gold-standard agents with raw vs governed comparisons
- `mission-control/` — fleet operations dashboard

## Conventions
- Manifests: `apiVersion: forgeos/v1` or `agentos/v1`
- Tool names: `company__*`, `mcp__server__tool`, `platform__*`, `agent__*`
- Every tool call goes through `runtime.check_tool()` → kernel decision
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed
