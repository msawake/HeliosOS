# ForgeOS Examples

Agent examples across 9 stack adapters — from hello-world to production-grade governed agents.

## Gold Standard Examples (Production-Ready)

Full governance with ForgeOS runtime controls, raw vs governed comparison, and real-world integrations:

| Agent | Controls | Framework | What It Does |
|-------|----------|-----------|-------------|
| [SRE GCP Auditor](sre-gcp-auditor/) | 10 + 2/tool | ADK + Gemini Flash | Daily audit of all GCP projects — infra, security, billing |
| [Drive Security Auditor](drive-security-auditor/) | 28 in 7 phases | ADK + Gemini Flash | Daily Google Drive sharing/permission risk scan |
| [Codebase Guardian](codebase-guardian/) | 15/iteration | Claude Sonnet | Always-on GitHub PR reviewer with security scanning |
| [Content Ops Pipeline](content-ops/) | 12/piece | Gemini + Claude | Multi-client content production with namespace isolation |
| [Competitive Intel](competitive-intel/) | 13 | Gemini + Claude | Dual-LLM research: Gemini scans, Claude analyzes |
| [SRE Ops Agent](sre-ops-agent/) | 11/iteration | Claude | Always-on infrastructure monitor with HITL |

Each gold-standard example includes:
- `agent.py` — full governance with numbered runtime controls
- `agent_raw.py` — same pipeline, zero governance (for comparison)
- `COMPARISON.md` — side-by-side code + risk table
- `manifest.yaml` — ForgeOS contract

## Hello-World (Stack Adapters)

```bash
PYTHONPATH=. python3 examples/run_all_hello_world.py
```

| Stack | Hello World | Runtime |
|-------|------------|---------|
| [ForgeOS](forgeos/) | `forgeos/hello_world.py` | Native agentic loop |
| [CrewAI](crewai/) | `crewai/hello_world.py` | `Crew.kickoff()` |
| [Google ADK](adk/) | `adk/hello_world.py` | `Runner.run_async()` |
| [OpenClaw](openclaw/) | `openclaw/hello_world.py` | HTTP gateway |

Additional adapters (LangChain/LangGraph, Anthropic SDK, Anthropic Managed, OpenAI Agents, Sandbox) are configured in `stacks/` — see [manifest examples](anthropic-agent/) and [docs](../docs/adapters-guide.md).

## Pattern Examples (YAML Manifests)

| Category | Examples | Demonstrates |
|----------|----------|-------------|
| [A2A](a2a/) | CEO supervisor, escalation router, research coordinator | Agent-to-agent communication |
| [Advanced](advanced/) | Multi-agent debate, self-improving agent | Async parallel, goal-directed loops |
| [Teams](teams/) | Sales squad, research pipeline, memory curator | Team orchestration patterns |
| [Filesystem](filesystem/) | Config validator, log analyzer, report writer | MCP filesystem tools |
| [Google Workspace](google-workspace/) | Email triage, calendar prep, Drive finder | Gmail/Drive/Calendar integration |
| [Google Ads](google-ads/) | Multi-client ads optimizer | Team + namespace policies |
| [Mixed](mixed/) | Budget guardian, compliance auditor, onboarding | Cross-stack business cases |
| [Platform](platform/) | Lead qualifier, PR reviewer, insurance comparator | Platform-level tool usage |

## Utility Scripts

| Script | Purpose |
|--------|---------|
| `run_all_hello_world.py` | Deploy + test all 4 hello-world agents |
| `deploy.py` | Deploy manifests by category/stack |
| `deploy_5_stack_agents.py` | Deploy 5 agents across 3 stacks |
| `deploy_call_center.py` | Deploy 10 humans + 8 agents (call center) |
| `full_platform_demo.py` | Exercise all kernel capabilities |
| `terminal_quickstart.py` | 3 ways to use ForgeOS (SDK / CLI / REST) |
| `file_tracker_agent.py` | Scan recent files with custom tools |
| `cleanup.py` | Undeploy all example agents |
| `test.py` | Integration test harness |

## Prerequisites

```bash
pip install -e ".[dev]"
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```
