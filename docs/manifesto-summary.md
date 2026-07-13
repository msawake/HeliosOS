# Helios OS — The Agentic Harness

**Control what your agents do. On any framework. Without changing their code.**

---

## The Problem

Every agent framework gives you a way to *write* agents. None gives you a way to *govern* them. Deploy 200 agents and one goes rogue — overspends its budget by 10x, calls tools it shouldn't, or accesses another department's data. No framework today has permissions, resource limits, or an audit trail.

## The Harness: 9 Capabilities No Other Framework Has

A framework tells an agent what to do. A **harness** controls *how* it does it. Install the Helios OS SDK, set one env var, and every agent — ADK, CrewAI, Claude SDK, LangChain, OpenAI, or your own — gets governance:

| Runtime Method | What It Does | Who Else Has This? |
|---|---|---|
| `check_tool()` | Check permission before action (proactive, not reactive) | **Nobody** does proactive checks |
| `budget()` / `reserve()` / `commit()` / `release()` | Two-phase budget transactions for money | **Nobody** — no budget primitives elsewhere |
| `checkpoint()` / `last_checkpoint()` | Save/restore agent state for crash recovery | LangGraph only; **nobody else** |
| `pending_signals()` / `signal()` | Cooperative preemption (SIGTERM/SIGSTOP) | **Nobody** — agents elsewhere just get killed |
| `request_capability()` / `revoke_capability()` | Time-limited delegation tokens with revocation | **Nobody** — OS-level capability security |
| `ask_human()` / `notify_human()` | Typed human-agent IPC with deadlines | Strands has basic `interrupt()`; this is richer |
| `contract()` / `process()` | Agent introspects its own budget and policy | **Nobody** — agents can't see their own constraints |
| `syscall()` | Unified entry point for all kernel operations | **Nobody** — the Linux syscall model for agents |

Works identically in-process (~0.1ms) or via HTTP to a remote kernel (~50ms). **One YAML manifest, enforced everywhere.**

## One Kernel, Any Framework — No Code Changes

Helios OS wraps tools in each framework's native type with a kernel gate inside. Agent code is never modified. Governance is declared in YAML.

| Framework | Native Tool Type | Helios OS Interception | Code Changed? |
|---|---|---|---|
| **Google ADK** | `FunctionTool(func)` | Async wrapper function | No |
| **CrewAI** | `BaseTool._run()` | Dynamic `BaseTool` subclass | No |
| **LangChain / LangGraph** | `BaseTool._run()` | `on_tool_start` callback (ONE handler, all tools) | No |
| **Anthropic Agent SDK** | MCP server tools | ONE `PreToolUse` hook for ALL tools | No |
| **Anthropic Managed** | Hosted sandbox | Pre-flight check at session level | No |
| **OpenAI Agents SDK** | Function tools | Function wrapper with kernel gate | No |
| **OpenClaw** | HTTP POST `/tool` | HTTP proxy server with kernel gate | No |
| **Sandbox (Docker)** | HTTP POST to API | API endpoint with token validation | No |
| **Helios OS native** | dict schema | Inline in agentic loop | No |

**How it works:** You write `stack: adk` and `tools: [read_json]` in YAML. Helios OS creates kernel-gated wrappers in the framework's native tool type and passes them to the agent constructor. The framework sees normal tools. The kernel sees every call.

---

## Why It Works: Built Like an Operating System

The harness can do what no other tool can because it's architected like a UNIX kernel — not a plugin layer.

| UNIX / Linux | Helios OS | Purpose |
|---|---|---|
| Process | Agent | Unit of execution with identity and lifecycle |
| Kernel | Kernel (6 subsystems) | Policy engine: permissions, budgets, policies |
| libc | Runtime SDK | Agent-side syscall interface (the harness API) |
| cgroups (cpu, memory) | Budgets (USD, tokens) | Resource limits per agent |
| File permissions (rwx) | Tool ACLs | Which tools each agent can call |
| SELinux / AppArmor | Policy Engine | Declarative rules that override permissions |
| Capabilities (CAP_*) | Capability Tokens | Time-limited, revocable delegation grants |
| Signals (SIGTERM) | Signals (SIGTERM, SIGEVICT) | Cooperative agent preemption |
| Namespaces (pid, net) | Namespaces | Data and agent isolation |
| IPC (sockets) | A2A Protocol | Agent-to-agent communication with ACLs |
| seccomp / Netfilter | Syscall Pipeline | 7-stage fixed-order admission control |
| init / systemd | Executor | Lifecycle management: deploy, schedule, recover |

## The Kernel: 6 Subsystems

| Subsystem | Linux Equivalent | What It Enforces |
|---|---|---|
| **AdmissionController** | `execve()` validation | Validates agent manifest before deployment |
| **PermissionManager** | DAC + ACLs (rwx) | Tool whitelist/denylist with wildcards; A2A peer ACLs |
| **BudgetManager** | cgroups (memory, cpu) | Daily USD cap, per-task cap, two-phase reserve/commit/release |
| **PolicyEngine** | SELinux / AppArmor | JSON-logic rules: `{deny_if: {op: "gt", field: "cost", value: 100}}` |
| **DataBoundaryManager** | Linux namespaces | Allowed/blocked namespace lists; PII policy (detect/mask/redact/block) |
| **CapabilityManager** | Linux capabilities | Opaque tokens with TTL + revocation; bypass ACLs for delegation |

## The Syscall Pipeline: 7 Fixed-Order Stages

Every agent action passes through this admission chain (like Netfilter for network packets):

```
identity --> capability --> quota --> policy --> boundary --> dispatch --> audit
               |              |         |           |
            can DENY     can RATE    can DENY    can MASK
                         _LIMIT
```

The order is enforced, not convention. You can plug in custom stages or set any stage to `None` to skip it.

---

## Remote Kernel: Network Transparency

Like NFS gives the same `read()`/`write()` API for local and remote files, Helios OS gives the same `runtime.check_tool()` in-process and across the network. Set `FORGEOS_API_URL` and the SDK automatically uses HTTP. 200 remote agents making 10 tool calls each = 2,000 HTTP requests — trivial for FastAPI, invisible next to 5-30s LLM calls.

## Licensing: Source-Available, Not Closed Source

Every line is readable. No binary blobs.

| Who | License | Production Use |
|---|---|---|
| Individuals (personal, non-commercial) | **BSL 1.1** | Free |
| Educational institutions (teaching, research) | **BSL 1.1** | Free |
| Companies (any commercial use) | **BSL 1.1** | Requires commercial license |

BSL auto-converts to Apache 2.0 on **2030-05-20**.

---

*Helios OS is not another agent framework. It's the harness that every framework is missing — the control layer for AI agents. Under the hood, it's built like an operating system, because that's the only architecture that scales.*

**github.com/msawake/HeliosOS** (canonical) · **github.com/makingscience-awake/forgeos** (mirror)
