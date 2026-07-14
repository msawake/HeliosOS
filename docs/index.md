# Helios OS

**The agentic harness.** Control what your agents do — on any framework, without changing their code. Helios OS deploys, orchestrates, and governs AI agents across nine framework adapters with a kernel, syscall pipeline, runtime SDK, and inter-agent protocols.

> Helios OS is the **harness**. Agents are the **processes** that run inside it.

## Where to start

<div class="grid cards" markdown>

- :material-rocket-launch: **New here?**

    Skim the [Quickstart](guides/quickstart.md), then read [Defining Agents](guides/defining-agents.md) — a worked example built around `examples/jira-greeter-v2`.

- :material-file-document-edit: **Writing an `agent.yaml`?**

    Use the [Agent Manifest Reference](reference/agent-manifest.md) for every field, default, and enum value.

- :material-server: **Wondering where your agent actually runs?**

    See [Runtime & Deployment](guides/runtime-and-deployment.md) — local process vs. in-platform vs. Cloud Run.

- :material-shield-key: **Want to understand the kernel?**

    Start at [Architecture › Overview](architecture/overview.md) and follow the syscall pipeline.

</div>

## Open-core scope

This public repository ships the **platform kernel, API, SDK, CLI, and examples**. It does **not** include:

- The Next.js operator dashboard (optional: [forgeos-dashboard](https://github.com/antonibergas-hue/forgeos-dashboard); integrated copy in [heliosos-enterprise](https://github.com/makingscience-awake/heliosos-enterprise))
- Corporate teams/workflows orchestration, billing, or production Pulumi
- Enterprise-only example agents (law-firm, SRE gold-standard auditors, etc.)

Local development uses the **REST API** and **`forgeos` / `forgeos mc` CLI**. The `--dashboard` bootstrap flag starts the **Django API**, not a browser UI.

## Layered model

```
Helios OS (the operating system)
  Kernel:    admission control, permissions, budgets, policies, data boundaries
  Syscall:   identity -> capability -> quota -> policy -> boundary -> dispatch -> audit
  Runtime:   SDK that agents use to interact with the kernel at runtime
  Platform:  registry, executor, scheduler, event bus, LLM routing, agentic loop
  Protocols: A2A (agent-to-agent), A2H (agent-to-human), MCP (agent-to-tool)

Agents (the processes)
  Defined by: manifest (name, framework, lifecycle, tools, boundaries)
  Deployed via: API, CLI, or SDK
  Run on: one of 9 framework adapters
  Governed by: kernel enforcement on every tool call, budget check, and agent call
```

## Building the docs locally

```bash
# with uv (recommended on this machine — system python is 3.9, project needs 3.11+)
uv pip install --system 'mkdocs-material>=9.5' 'mkdocs-awesome-pages-plugin>=2.9'

# or, if you have a 3.11+ pip on PATH
pip install -e ".[docs]"

mkdocs serve   # http://127.0.0.1:8000
```
