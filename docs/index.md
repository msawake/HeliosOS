# ForgeOS

**The agentic harness.** Control what your agents do — on any framework, without changing their code. ForgeOS deploys, orchestrates, and governs AI agents across nine framework adapters with a kernel, syscall pipeline, runtime SDK, and inter-agent protocols.

> ForgeOS is the **harness**. Agents are the **processes** that run inside it.

## Where to start

<div class="grid cards" markdown>

- :material-rocket-launch: **New here?**

    Skim the [Quickstart](guides/quickstart.md), then read [Defining Agents](guides/defining-agents.md) — a worked example built around `examples/sre-gcp-auditor`.

- :material-file-document-edit: **Writing an `agent.yaml`?**

    Use the [Agent Manifest Reference](reference/agent-manifest.md) for every field, default, and enum value.

- :material-server: **Wondering where your agent actually runs?**

    See [Runtime & Deployment](guides/runtime-and-deployment.md) — local process vs. in-platform vs. Cloud Run.

- :material-shield-key: **Want to understand the kernel?**

    Start at [Architecture › Overview](architecture/overview.md) and follow the syscall pipeline.

</div>

## Layered model

```
ForgeOS (the operating system)
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
