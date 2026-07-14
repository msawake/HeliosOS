# Helios OS Examples

Agent examples across stack adapters — from hello-world to production-style governed agents.

> **Open-core scope:** enterprise-only examples (law-firm, SRE gold-standard auditors, codebase-guardian, etc.) are **not** in this public tree. They remain in [heliosos-enterprise](https://github.com/makingscience-awake/heliosos-enterprise).

## Hello-World (stack adapters)

```bash
PYTHONPATH=. python3 examples/run_all_hello_world.py
```

| Stack | Hello World | Runtime |
|-------|------------|---------|
| [Helios OS](forgeos/) | `forgeos/hello_world.py` | Native agentic loop |
| [CrewAI](crewai/) | `crewai/hello_world.py` | `Crew.kickoff()` |
| [Google ADK](adk/) | `adk/hello_world.py` | `Runner.run_async()` |
| [OpenClaw](openclaw/) | `openclaw/hello_world.py` | HTTP gateway |

Additional adapters (LangChain/LangGraph, Anthropic SDK, Anthropic Managed, OpenAI Agents, Sandbox) are configured in `stacks/` — see [anthropic-agent/](anthropic-agent/) and [docs/adapters-guide.md](../docs/adapters-guide.md).

## Pattern examples (YAML manifests)

| Category | Examples | Demonstrates |
|----------|----------|-------------|
| [A2A](a2a/) | CEO supervisor, escalation router, research coordinator | Agent-to-agent communication |
| [Advanced](advanced/) | Multi-agent debate, self-improving agent | Async parallel, goal-directed loops |
| [Teams](teams/) | Sales squad, research pipeline, memory curator | Team orchestration patterns |
| [Filesystem](filesystem/) | Config validator, log analyzer, report writer | MCP filesystem tools |
| [Google Workspace](google-workspace/) | Email triage, calendar prep, Drive finder | Gmail/Drive/Calendar integration |
| [Mixed](mixed/) | Budget guardian, compliance auditor, onboarding | Cross-stack business cases |
| [Platform](platform/) | Lead qualifier, PR reviewer, insurance comparator | Platform-level tool usage |
| [Jira](jira-greeter-v2/) | Ticket greeter with A2H approval | MCP + human-in-the-loop |

## Utility scripts

| Script | Purpose |
|--------|---------|
| `run_all_hello_world.py` | Deploy + test all 4 hello-world agents |
| `deploy.py` | Deploy manifests by category/stack |
| `deploy_5_stack_agents.py` | Deploy 5 agents across 3 stacks |
| `deploy_call_center.py` | Deploy call-center demo agents |
| `full_platform_demo.py` | Exercise kernel capabilities |
| `terminal_quickstart.py` | SDK / CLI / REST quickstart |
| `file_tracker_agent.py` | Scan recent files with custom tools |
| `cleanup.py` | Undeploy all example agents |
| `test.py` | Integration test harness |

## Prerequisites

```bash
pip install -e ".[dev]"
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
export FORGEOS_API_URL=http://localhost:5000
forgeos deploy examples/forgeos/hello-world.yaml
```

Use the API and `forgeos` CLI — the Next.js dashboard is optional and lives in a [separate repo](https://github.com/antonibergas-hue/forgeos-dashboard).