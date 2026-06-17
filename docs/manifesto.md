# Helios OS — The Agentic Harness

## Control what your agents do. On any framework. Without changing their code.

---

## 1. The Problem: Agents Without an Operating System

In 1969, computers ran one program at a time. No isolation, no permissions, no resource limits. If a program went rogue, it took down the machine. Then UNIX arrived with a radical idea: programs shouldn't run on bare metal — they should run inside an operating system that manages resources, enforces permissions, and isolates failures.

In 2025, AI agents are where programs were in 1969.

Every agent framework today — LangGraph, CrewAI, Google ADK, Strands, OpenAI Agents SDK — gives you a way to write agents. None gives you a way to govern them. They're like writing assembly for bare metal:

- **No process isolation.** One agent's failure cascades to others.
- **No permissions.** Every agent can call every tool with every input.
- **No resource limits.** An agent can spend $10,000 in tokens before anyone notices.
- **No audit trail.** Who called what, when, with what input? Nobody knows.
- **No signals.** You can't gracefully stop an agent mid-task.

What happens when you deploy 200 agents across sales, marketing, finance, and operations — and one of them starts sending emails to your entire customer database? Or exceeds its budget by 10x? Or calls a tool it was never supposed to access?

In the server world, we solved this 50 years ago. Helios OS applies those lessons to agents.

---

## 2. The Harness: 9 Capabilities No Other Framework Has

A framework tells an agent what to do. A **harness** controls *how* it does it — enforcing budgets, checking permissions, delivering signals, saving checkpoints — regardless of which framework drives the LLM loop.

Install the Helios OS SDK, set one env var, and every agent — ADK, CrewAI, LangChain, Claude SDK, OpenAI, or your own — gets governance:

| Runtime Method | OS Analogy | Who Else Has This? |
|---|---|---|
| `check_tool()` | `access()` — check permission before action | Strands has hooks (reactive). Helios OS is **proactive**. |
| `budget()` / `reserve()` / `commit()` / `release()` | Two-phase commit — a database transaction for money | **Nobody.** ADK, CrewAI, Strands have no budget primitives. |
| `checkpoint()` / `last_checkpoint()` | Process checkpointing — like CRIU for containers | LangGraph has checkpoints. **Nobody else** in the agent space. |
| `pending_signals()` / `signal()` | POSIX signals — SIGTERM/SIGSTOP for cooperative preemption | **Nobody.** Agents in other frameworks just get killed. |
| `request_capability()` / `revoke_capability()` | Capability tokens — unforgeable file descriptors | **Nobody.** OS research (capability-based security) applied to agents. |
| `ask_human()` / `notify_human()` | IPC with a human process — human as first-class agent | Strands has `interrupt()`. Helios OS is richer (typed responses, deadlines, channels). |
| `contract()` / `process()` | `/proc/self/status` — introspect own constraints | **Nobody.** Agents in other frameworks can't see their own budget or policy. |
| `syscall()` | Unified system call — one entry point for all kernel operations | **Nobody.** The Linux syscall model applied to agents. |
| `bind()` / `unbind()` | Thread-local storage — identity scoped to async task | Standard pattern, uniquely applied to agent identity. |

The same `runtime.check_tool()` call works identically whether the agent runs in-process (~0.1ms direct Python call) or on a separate Cloud Run (~50ms HTTP to the kernel). Developers write one set of governance rules in YAML. The runtime enforces them everywhere.

---

## 3. Why It Works: Built Like an Operating System

The harness can do what no other tool can because it's architected like a UNIX kernel — not a plugin layer. This isn't a metaphor — it's a direct structural mapping. Every concept that makes UNIX work has an exact equivalent in agent orchestration:

```
┌──────────────────────────────────┬────────────────────────────────────────────┐
│           UNIX / Linux           │                  Helios OS                  │
├──────────────────────────────────┼────────────────────────────────────────────┤
│ Process                          │ Agent                                      │
│ Binary (ELF)                     │ Manifest (agent.yaml)                      │
│ Kernel                           │ Kernel (permissions + budgets + policies)  │
│ libc                             │ Runtime (agent-side syscall interface)     │
│ init / systemd                   │ Executor (lifecycle management)            │
│ bash shell                       │ Agentic Loop (LLM → tool → result cycle)  │
│ Namespaces (pid, net, ipc)       │ Namespaces (logical agent isolation)       │
│ System calls                     │ Tool calls (checked by kernel)             │
│ File permissions (rwx)           │ Tool ACLs (allowed/denied lists)           │
│ cgroups (cpu, memory limits)     │ Budgets (daily_usd, per_task_usd limits)  │
│ SELinux / AppArmor               │ Policy Engine (declarative JSON-logic)     │
│ Capabilities (CAP_*)             │ Capability Tokens (runtime delegation)     │
│ Signals (SIGTERM, SIGSTOP)       │ Signals (SIGTERM, SIGSTOP, SIGEVICT)      │
│ IPC (sockets, message queues)    │ A2A (agent-to-agent calls)                │
│ /proc filesystem                 │ Registry (agent discovery + status)        │
│ seccomp / Netfilter              │ Syscall Pipeline (7-stage admission)       │
│ Core dumps                       │ Checkpoints (crash recovery)               │
│ Hardware abstraction (x86/ARM)   │ Stack Adapters (9 framework runtimes)     │
│ systemd targets                  │ Team Manifests (deploy agent groups)       │
│ NFS (network filesystem)         │ Remote Kernel (same API, HTTP backend)    │
└──────────────────────────────────┴────────────────────────────────────────────┘
```

Helios OS is not another agent framework. It's the layer that sits **between** the framework and the agent — managing lifecycle, enforcing policy, tracking resources, and providing the primitives that make multi-agent systems safe.

---

## 4. The Kernel — 6 Subsystems

The Helios OS kernel is the policy decision point for every meaningful agent action. Like the Linux kernel, it sits between the agent (userspace) and the resources (tools, data, other agents). No agent touches a resource without the kernel's approval.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Agent (userspace)                         │
│  runtime.check_tool("send_email", {"to": "user@..."})       │
└──────────────────────────────┬──────────────────────────────┘
                               │ syscall
┌──────────────────────────────▼──────────────────────────────┐
│                        KERNEL                                │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐ │
│  │  1. Admission   │  │ 2. Permissions │  │  3. Budgets   │ │
│  │  Controller     │  │    Manager     │  │    Manager    │ │
│  │                 │  │                │  │               │ │
│  │  Validates      │  │  Tool ACLs     │  │  daily_usd    │ │
│  │  contracts      │  │  A2A ACLs      │  │  per_task_usd │ │
│  │  before deploy  │  │  Data access   │  │  two-phase    │ │
│  │                 │  │  Wildcards     │  │  reservation  │ │
│  └────────────────┘  └────────────────┘  └───────────────┘ │
│                                                              │
│  ┌────────────────┐  ┌────────────────┐  ┌───────────────┐ │
│  │  4. Policy      │  │ 5. Data        │  │ 6. Capability │ │
│  │  Engine         │  │    Boundaries  │  │    Manager    │ │
│  │                 │  │                │  │               │ │
│  │  JSON-logic     │  │  Namespace     │  │  Opaque       │ │
│  │  rules          │  │  isolation     │  │  tokens       │ │
│  │  deny_if:       │  │  PII policy    │  │  TTL + revoke │ │
│  │  {op, field,    │  │  (detect/mask/ │  │  Short-circuit│ │
│  │   value}        │  │   redact/block)│  │  ACL checks   │ │
│  └────────────────┘  └────────────────┘  └───────────────┘ │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼ allow / deny / rate_limit / mask
┌──────────────────────────────────────────────────────────────┐
│                    Tool Execution                             │
│            send_email({"to": "user@..."})                    │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 AdmissionController — like `execve()` validation

Before a program runs on Linux, the kernel validates the ELF binary: is it a valid executable? Does the user have execute permission? Are the shared libraries available?

Helios OS does the same for agents. Before an agent deploys, the AdmissionController validates its contract:

- Is the name valid? (regex: `^[a-zA-Z][a-zA-Z0-9_-]{1,63}$`)
- Is the (namespace, name) pair unique?
- Is the stack recognized? (forgeos, crewai, adk, openclaw, anthropic-agent-sdk, anthropic-managed)
- Is a schedule provided for scheduled agents?
- Are declared tool dependencies resolvable?
- Are agent dependencies present? (like checking shared library availability)

```python
result = kernel.admit(contract)
# AdmissionResult(admitted=True, warnings=["tool 'custom__xyz' not found"])
```

If admission fails, the agent doesn't deploy. Period.

### 3.2 PermissionManager — like DAC (rwx) + ACLs

Linux has file permissions (read/write/execute for owner/group/other) and Access Control Lists. Helios OS has tool permissions and A2A peer ACLs.

**Tool permissions** are declared in the manifest:

```yaml
spec:
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__record_metric
        - mcp__filesystem__read_*     # wildcards supported
      denied:
        - company__send_email          # explicitly blocked
        - mcp__filesystem__write_*     # no writes
```

When an agent calls `runtime.check_tool("company__send_email")`, the kernel:
1. Checks the allowed list (with wildcard matching)
2. Checks the denied list (deny overrides allow)
3. Returns ALLOW or DENY

**A2A permissions** control which agents can call which:

```yaml
spec:
  capabilities:
    a2a:
      canCall:
        - namespace: sales
          agents: ["lead-researcher", "outreach-writer"]
      canBeCalledBy:
        - namespace: sales
          agents: ["sales-manager"]
```

This is the agent equivalent of socket permissions — who can connect to whom.

### 3.3 BudgetManager — like cgroups (resource limits)

Linux cgroups limit how much CPU, memory, and I/O a process can consume. Helios OS budgets limit how much money and compute an agent can spend.

```yaml
spec:
  boundaries:
    budgets:
      daily_usd: 5.00          # max $5/day (like memory.max in cgroups)
      per_task_usd: 0.50        # max $0.50 per invocation (like cpu.max)
      max_tokens_per_run: 50000 # token cap per run
      max_tool_calls_per_run: 100
```

**Two-phase reservation** (like memory accounting in the kernel):

```python
# Phase 1: Reserve before the operation
ticket = await runtime.reserve(estimated_cost_usd=0.05)
if ticket is None:
    # Budget exhausted — can't proceed
    return

try:
    # Execute the expensive operation
    result = await call_llm(prompt)
    actual_cost = calculate_cost(result)
    
    # Phase 2: Commit with actual cost
    await runtime.commit(ticket, actual_cost_usd=actual_cost)
except Exception:
    # Release reservation on failure
    await runtime.release(ticket)
```

When an agent hits its daily budget, the kernel returns `rate_limit` — the agent's tools stop working until the next day. Like an OOM kill, but for dollars.

### 3.4 PolicyEngine — like SELinux/AppArmor (Mandatory Access Control)

Linux has Discretionary Access Control (file permissions set by the owner) and Mandatory Access Control (system-wide policies that override per-file permissions). Helios OS has both:

- **Permissions** (§3.2) = DAC — the agent's owner defines what it can do
- **Policies** (§3.4) = MAC — the platform enforces rules the agent can't override

```yaml
spec:
  governance:
    policies:
      - name: no_mass_email
        ref: policies/no-mass-email.json
      - name: financial_threshold
        ref: policies/financial-approval.json
```

Policy rules use JSON-logic expressions:

```json
{
  "deny_if": {
    "op": "gt",
    "field": "tool_input.recipients_count",
    "value": 50
  }
}
```

If any policy denies, the action is blocked — regardless of what the agent's own permissions say. Just like SELinux can prevent root from writing to `/etc/shadow`.

### 3.5 DataBoundaryManager — like Linux namespaces

Linux namespaces isolate processes from each other: a process in one PID namespace can't see processes in another. Helios OS namespaces isolate agents from each other's data:

```yaml
spec:
  boundaries:
    data:
      allowed_namespaces: [sales, marketing]   # can read from these
      blocked_namespaces: [finance, hr]         # explicitly blocked
      pii_policy: mask                          # detect/mask/redact/block
```

A sales agent can access sales data and marketing data, but if it tries to query the finance namespace:

```python
decision = await runtime.check_data("finance")
# KernelDecision(action="deny", reason="Namespace 'finance' is blocked")
```

**PII policy** adds another layer — like Linux's `prctl(PR_SET_DUMPABLE, 0)` which prevents core dumps from leaking sensitive memory. Helios OS can detect, mask, redact, or block PII in tool inputs and outputs.

### 3.6 CapabilityManager — like Linux capabilities (CAP_*)

In Linux, a process doesn't need full root access to bind to port 80 — it just needs `CAP_NET_BIND_SERVICE`. This fine-grained approach replaces the all-or-nothing setuid model.

Helios OS capability tokens work the same way. Instead of giving an agent permanent A2A permission to call the CFO agent, you issue a time-limited token:

```python
# Sales manager needs to ask CFO for budget approval — just this once
token = await runtime.request_capability(
    target="finance/cfo",
    verb="a2a.invoke",
    ttl=300,              # expires in 5 minutes
    metadata={"reason": "Q4 budget review"},
)

# The token bypasses ACL checks — like a capability in Capsicum
# When the token expires or is revoked, access is gone
await runtime.revoke_capability(token.id)
```

**Positive authority model:** A valid token grants access *regardless of ACL*. This enables runtime delegation without redeploying the callee's manifest. The security model is revocation — delete the token, and access disappears.

---

## 5. The Syscall Pipeline — 7 Stages

When a Linux process calls `write(fd, buf, count)`, the kernel doesn't just write. It checks file permissions, disk quotas, mandatory locks, SELinux labels, and audits the operation. These checks happen in a fixed order.

Helios OS has the same concept: the **syscall pipeline**. Every agent action flows through 7 stages in a fixed, immutable order:

```
┌──────────┐  ┌────────────┐  ┌───────┐  ┌────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐
│ identity │─▶│ capability │─▶│ quota │─▶│ policy │─▶│ boundary │─▶│ dispatch │─▶│ audit │
└──────────┘  └────────────┘  └───────┘  └────────┘  └──────────┘  └──────────┘  └───────┘
                  │                │          │            │
               can return      can return  can return  can return
                 DENY          RATE_LIMIT    DENY         MASK
```

### Why the order is fixed

1. **Identity** first — you must know WHO is asking before checking anything else
2. **Capability** before quota — a valid token bypasses ACL checks (short-circuit)
3. **Quota** before policy — check if the agent can afford it before evaluating rules
4. **Policy** before boundary — declarative rules override data access
5. **Boundary** before dispatch — mask/redact PII before the tool sees the input
6. **Dispatch** executes the actual operation
7. **Audit** records the decision and result

**You cannot reorder stages.** This is enforced by the pipeline, not by convention. Like Netfilter's chain order (PREROUTING → INPUT → FORWARD → OUTPUT → POSTROUTING), the stages exist in a specific order because correctness depends on it.

### Pluggable stages

Each stage is a callable that takes a `Syscall` and returns `KernelDecision | None`:

```python
def my_custom_policy_stage(syscall: Syscall) -> KernelDecision | None:
    if syscall.verb == "tool.call" and "dangerous" in syscall.object:
        return KernelDecision.deny("Custom policy: dangerous tools blocked")
    return None  # pass to next stage

pipeline.set_stage("policy", my_custom_policy_stage)
```

Return `None` to pass. Return a `KernelDecision` to short-circuit. The pipeline stops on the first non-`None` result (except audit, which always runs).

---

## 6. The Process Table — Lifecycle of an Agent

### Phase Machine

Every process in Linux has a state: running, sleeping, stopped, zombie. Helios OS agents have phases:

```
                    ┌──────────────────────────────────────────────────────────┐
                    │              Agent Phase Machine                          │
                    │                                                           │
                    │  PENDING ──▶ ADMITTED ──▶ STARTING ──▶ RUNNING           │
                    │                                          │   ↕            │
                    │                                          │ DRAINING       │
                    │                                          │   │            │
                    │                                          ▼   ▼            │
                    │                                        STOPPED           │
                    │                                                           │
                    │  From any non-terminal phase:                             │
                    │    ──▶ FAILED        (unrecoverable error)               │
                    │    ──▶ QUARANTINED   (too many crashes, manual review)   │
                    │    ──▶ EVICTED       (preempted for resource reasons)    │
                    └──────────────────────────────────────────────────────────┘
```

| Phase | Linux Equivalent | When |
|-------|-----------------|------|
| PENDING | TASK_NEW | Agent manifest submitted, not yet validated |
| ADMITTED | — | Kernel validated the contract, ready to start |
| STARTING | TASK_RUNNING (loading) | Adapter creating agent, scaffolding files |
| RUNNING | TASK_RUNNING | Agent actively processing or waiting for work |
| DRAINING | TASK_INTERRUPTIBLE | Agent finishing current work, refusing new tasks |
| STOPPED | EXIT_ZOMBIE → reaped | Agent terminated normally |
| FAILED | Core dumped | Unrecoverable error during execution |
| QUARANTINED | SIGSTOP'd by admin | Too many crashes; requires human review |
| EVICTED | OOM killed | Preempted by the kernel for resource reasons |

### Resource Accounting

Like `/proc/<pid>/stat` tracks CPU time, memory, and I/O for each process, Helios OS tracks 5 resource dimensions for each agent:

```python
process = await runtime.process()
# ProcessSnapshot(
#     pid="sdr-01",
#     phase="running",
#     tokens_in=15000,          # input tokens consumed
#     tokens_out=8000,          # output tokens generated
#     dollars=2.50,             # USD spent
#     tool_calls=47,            # tools invoked
#     wallclock_ms=180000.0,    # wall clock time
#     pending_signals=[],
# )
```

### Signals

Linux signals (SIGTERM, SIGSTOP, SIGKILL) provide cooperative and forceful process control. Helios OS has the same:

| Signal | Linux Equivalent | Behavior |
|--------|-----------------|----------|
| **SIGTERM** | SIGTERM | "Please stop gracefully." Agent checks at tool boundaries and exits cleanly. |
| **SIGSTOP** | SIGSTOP | "Pause now." Agent stops accepting new work but finishes current tool call. |
| **SIGEVICT** | SIGKILL (softer) | "You're being preempted." Agent saves checkpoint and exits. |

Signals are **cooperative**, not forceful. The agent checks for pending signals at tool boundaries:

```python
# Inside an autonomous agent's main loop:
while True:
    signals = await runtime.pending_signals()
    if "SIGTERM" in signals:
        await cleanup()
        break
    if "SIGSTOP" in signals:
        await runtime.checkpoint({"step": current_step})
        break

    # ... do work ...
```

An operator (or the kernel's budget manager) sends signals:

```python
# Operator: "Stop the runaway agent"
await runtime.signal(target_pid="sdr-01", signal_name="SIGTERM", reason="Budget exceeded")
```

---

## 7. The Runtime (libc) — Agent-Side Interface

In Linux, user programs don't talk to the kernel directly — they call libc functions like `open()`, `read()`, `write()`, `fork()`. The C library translates these into system calls.

Helios OS has the **Runtime** — the agent-side library that translates high-level operations into kernel checks. Every agent gets a `runtime` singleton that carries its identity and mediates all interactions with the kernel.

### The Full API, Mapped to POSIX

```python
from forgeos_sdk import runtime
```

| `runtime.method()` | POSIX Equivalent | What It Does |
|---|---|---|
| `bind(agent_id, namespace)` | `setuid()` / `setns()` | Set the calling agent's identity for this async context |
| `unbind(token)` | `seteuid(original)` | Restore the previous identity |
| `agent_id` | `getpid()` | Get the current agent's process ID |
| `namespace` | `getns()` (hypothetical) | Get the current namespace |
| `is_bound` | — | Is identity set? |
| `check_tool(name, input)` | `access(path, mode)` | Check permission before calling a tool |
| `check_a2a(ns, name)` | `connect()` permission check | Check if allowed to call another agent |
| `check_data(namespace)` | `open()` permission check | Check if allowed to access a namespace's data |
| `syscall(verb, target, args)` | `syscall()` | Run through the full 7-stage admission pipeline |
| `budget()` | `getrlimit()` | Query current resource limits and usage |
| `reserve(cost)` | `mmap(MAP_PRIVATE)` | Reserve resources before using them |
| `commit(ticket, cost)` | `msync()` / finalize | Finalize reservation with actual usage |
| `release(ticket)` | `munmap()` | Release unused reservation |
| `checkpoint(state)` | `fork()` + core dump | Save agent state at a stable boundary |
| `last_checkpoint()` | Read core dump | Restore saved state after crash |
| `request_capability(target, verb, ttl)` | `capset()` | Request a time-limited capability token |
| `revoke_capability(token_id)` | `prctl(PR_CAPBSET_DROP)` | Revoke a capability |
| `list_capabilities()` | `capget()` | List active capabilities |
| `pending_signals()` | `sigpending()` | Check for queued signals |
| `signal(pid, name, reason)` | `kill(pid, sig)` | Send a signal to another agent |
| `contract()` | `cat /proc/self/status` | Read your own deployment contract |
| `process()` | `getrusage()` | Read your own resource usage |
| `ask_human(ns, name, question)` | `write(STDOUT_FILENO)` | Ask a human operator for input |
| `notify_human(ns, name, message)` | `syslog()` to console | Send notification to human (no response needed) |
| `audit(event, details)` | `audit_log_user_avc()` | Record a custom audit event |

### Example: Agent Using the Runtime

```python
from forgeos_sdk import runtime

# Check if we can send email before trying
decision = await runtime.check_tool("send_email", {"to": "customer@..."})
if decision.denied:
    print(f"Blocked: {decision.reason}")
    return

# Check budget before expensive LLM call
budget = await runtime.budget()
if budget.remaining_usd is not None and budget.remaining_usd < 0.10:
    print("Budget low — switching to cheaper model")

# Reserve budget for the operation
ticket = await runtime.reserve(estimated_cost_usd=0.05)
if ticket is None:
    print("Budget exhausted")
    return

try:
    result = await expensive_llm_call()
    await runtime.commit(ticket, actual_cost_usd=0.03)
except Exception:
    await runtime.release(ticket)
    raise

# Check for operator signals
signals = await runtime.pending_signals()
if "SIGTERM" in signals:
    await runtime.checkpoint({"completed_items": processed_count})
    return  # exit gracefully

# Record what we did
await runtime.audit("email_sent", {"to": "customer@...", "subject": "Follow up"})
```

---

## 8. The Manifest — Agent Binary Format

When you compile a C program, the compiler produces an ELF binary with headers, sections, symbol tables, and dependency lists. When you declare a Helios OS agent, you write a YAML manifest with metadata, spec sections, and dependency declarations.

### ELF ↔ Manifest Mapping

| ELF Section | Manifest Section | What It Contains |
|---|---|---|
| ELF header (magic, class, ABI) | `apiVersion`, `kind` | Format version and type |
| Program header (entry point, arch) | `metadata` (name, namespace, version) | Identity and addressing |
| `.interp` (interpreter path) | `spec.llm` (chat_model, provider) | Which LLM "interprets" this agent |
| Dynamic section (.so deps) | `spec.tools` | Tools this agent can call |
| `.note` (capabilities) | `spec.capabilities` | Permissions (tool ACLs, A2A peers) |
| `ulimit` / cgroup config | `spec.boundaries` | Resource limits (USD, tokens, namespaces) |
| SELinux context | `spec.governance` | Policies, HITL approvals, audit level |
| `ldd` output (shared libs) | `spec.dependencies` | Required agents and MCP servers |
| `.text` (code) | `spec.system_prompt` | The agent's instructions |
| Process state | `status` | Phase, conditions, runtime metrics |

### A Complete Agent Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: lead-researcher
  namespace: sales
  version: "1.2.0"
  description: "Discovers and qualifies B2B leads using CRM and knowledge base"
  labels:
    department: sales
    tier: worker

spec:
  # Which "interpreter" runs this agent
  runtime:
    framework: adk                # Google ADK runtime
    image: europe-west1-docker.pkg.dev/myproject/agents/lead-researcher:1.2.0

  # Execution lifecycle
  lifecycle:
    type: always_on               # like a daemon (systemd Type=simple)
    replicas: 1
    restart_policy: OnFailure

  # The "interpreter" — which LLM
  llm:
    chat_model: gemini-2.0-flash
    provider: google

  # The "system calls" this agent can make
  capabilities:
    tools:
      allowed:
        - platform__crm_search_leads
        - company__search_knowledge
        - knowledge__catalog
        - knowledge__search
      denied:
        - company__send_email       # researchers don't send emails
        - platform__crm_delete_*    # no deleting CRM records
    a2a:
      canCall:
        - namespace: sales
          agents: [outreach-writer]   # can delegate to the writer
      canBeCalledBy:
        - namespace: sales
          agents: [sales-manager]     # only the manager can invoke me

  # Resource limits (like cgroups)
  boundaries:
    budgets:
      daily_usd: 3.00
      per_task_usd: 0.25
      max_tokens_per_run: 50000
    data:
      allowed_namespaces: [sales, marketing]
      blocked_namespaces: [finance, hr]
      pii_policy: mask

  # Governance (like SELinux policies)
  governance:
    audit_level: full
    policies:
      - name: no_competitor_research
        ref: policies/no-competitor.json
    human_in_loop:
      - event: crm_update
        approvers: [sales-manager]
        sla_hours: 4

  # Dependencies (like shared libraries)
  dependencies:
    agents:
      - namespace: sales
        name: outreach-writer
        optional: true
    mcp_servers:
      - filesystem
```

---

## 9. Stack Adapters — Hardware Abstraction Layer

Linux runs on x86, ARM, RISC-V, MIPS — the same kernel, different hardware. Helios OS runs agents on 9 different runtimes — the same kernel, different agent frameworks.

### The Adapter Pattern

Each framework has its own way of defining tools. Helios OS **wraps** tools in each framework's native type with a kernel gate injected inside:

```
ORIGINAL (without Helios OS):
  LLM → "call read_json" → Framework → read_json() → result

WITH Helios OS (no agent code change):
  LLM → "call read_json" → Framework → [Helios OS Wrapper] → result
                                              │
                                    ┌─────────┴──────────┐
                                    │ kernel.check_tool() │
                                    │ → ALLOW: execute    │
                                    │ → DENY: error msg   │
                                    └────────────────────┘
```

### Interception Table

| Framework | Native Tool Type | Helios OS Wraps As | Kernel Gate Inside | Agent Code Changed? |
|---|---|---|---|---|
| **Helios OS** | dict schema | `_execute_tool()` | Inline in agentic loop | No |
| **Google ADK** | `FunctionTool(func)` | Async wrapper → `FunctionTool` | Inside wrapper, before `execute()` | No |
| **CrewAI** | `BaseTool._run()` | `ForgeOSTool(BaseTool)` subclass | Inside `_run()`, before `execute()` | No |
| **LangChain / LangGraph** | `BaseTool._run()` | `on_tool_start` callback handler | ONE callback gates ALL tools | No |
| **OpenAI Agents SDK** | Function tools | Function wrapper with kernel gate | Inside wrapper, before `execute()` | No |
| **Anthropic SDK** | MCP server `@tool` | `PreToolUse` hook (ONE hook for ALL tools) | Global hook, not per-tool | No |
| **Anthropic Managed** | Hosted sandbox | Pre-flight check at session level | Before API call (session boundary) | No |
| **OpenClaw** | HTTP POST `/tool` | `ToolProxyServer` HTTP handler | Inside request handler | No |
| **Sandbox** | HTTP POST to API | API endpoint handler | Token validation + kernel check | No |

### Why It Works Without Changing Agent Code

Each framework has an extension point for tools. Helios OS exploits these points:

- **ADK:** `FunctionTool` accepts any async function. Helios OS creates a wrapper function with the correct `__name__` and `__doc__` — ADK can't tell the difference.
- **CrewAI:** `BaseTool` is a Pydantic class. Helios OS creates a dynamic subclass where `_run()` checks the kernel first. CrewAI can't tell the difference.
- **LangChain / LangGraph:** LangChain fires `on_tool_start()` before every tool via its callback system. Helios OS registers ONE `ForgeOSKernelCallback` with `raise_error=True` — if the kernel denies, the callback raises `ToolException` and the tool is blocked. Works with `AgentExecutor`, `create_react_agent`, and any `ToolNode`.
- **OpenAI Agents SDK:** Function tools accept any callable. Helios OS wraps each function with a kernel gate — same pattern as ADK.
- **Anthropic Agent SDK:** The SDK has a `PreToolUse` hook system. Helios OS registers ONE hook that intercepts ALL tools. No per-tool wrapping needed.
- **Anthropic Managed:** Agents run in Anthropic's hosted sandbox. Helios OS checks budget and permissions BEFORE submitting the session to the API (pre-flight check at session boundary).
- **OpenClaw:** The gateway calls tools via HTTP. Helios OS runs a proxy server on localhost. The gateway POSTs to it. Our handler checks the kernel.
- **Sandbox:** The container receives a `FORGEOS_API_URL`. Tool calls go through the API. Kernel checks happen at the endpoint.

**Everything is driven by the YAML manifest.** The developer writes `stack: adk` and `tools: [read_json, write_json]`, and Helios OS handles the wrapping automatically.

---

## 10. A2A — Inter-Process Communication

When processes need to communicate in UNIX, they use IPC mechanisms: pipes, sockets, message queues, shared memory. Helios OS agents communicate via the A2A (Agent-to-Agent) protocol.

### IPC Mapping

| Helios OS A2A | UNIX IPC | Pattern |
|---|---|---|
| `agent__call(ns, name, task)` | `send()` on connected socket | Synchronous: send task, wait for result |
| `agent__async_call(ns, name, task)` | `msgsnd()` to message queue | Fire-and-forget: returns job_id immediately |
| `agent__await(job_id)` | `msgrcv()` blocking read | Wait for async result |
| `agent__list_available(ns)` | `getaddrinfo()` / DNS | Discover available agents |
| `DelegationContext` | Socket address + credentials | Tracks call chain, depth, budgets |
| `IsolationPolicy` | `SO_PEERCRED` | Controls what context flows between caller/callee |

### Call Chain Safety

Like socket connections, A2A calls can create chains: A calls B calls C calls D. Without limits, this leads to infinite recursion (stack overflow) or circular dependencies (deadlock).

Helios OS prevents this with:

1. **Depth limit** (default 5) — like `RLIMIT_NPROC` for processes
2. **Cycle detection** — `DelegationContext.would_cycle(agent_id)` checks the call path
3. **ACL enforcement** — kernel checks `canCall`/`canBeCalledBy` at every hop
4. **Budget propagation** — remaining budget flows down the chain; child can't overspend parent

### Context Isolation

By default, A2A calls are **isolated**: the callee gets a fresh context (no inherited conversation history, no caller's context dict). Only the task and delegation metadata flow through. This is like `fork()` creating a new address space — the child doesn't see the parent's stack.

---

## 11. Teams — systemd Unit Groups

A systemd target groups related services: `multi-user.target` starts networking, logging, and SSH together. A Helios OS Team Manifest groups related agents:

```yaml
apiVersion: forgeos/v1
kind: Team
metadata:
  name: sales-squad
  namespace: sales
spec:
  orchestration: supervisor     # boss delegates to workers

  agents:
    - name: sales-manager
      role: supervisor
      llm: {chat_model: claude-opus-4-6}
      tools: [agent__call, agent__list_available]

    - name: lead-researcher
      role: worker
      tools: [company__search_knowledge]

    - name: outreach-writer
      role: worker
      tools: [company__send_email]
```

`forgeos deploy sales-squad.yaml` deploys all three agents with pre-wired A2A permissions:
- Manager gets `canCall: [lead-researcher, outreach-writer]`
- Workers get `canBeCalledBy: [sales-manager]`
- Workers cannot call each other (supervisor pattern)

### Orchestration Patterns

| Pattern | systemd Equivalent | How Agents Relate |
|---|---|---|
| **supervisor** | `Type=notify` + `Wants=` | Boss calls workers. Workers only respond to boss. |
| **sequential** | `After=` chain | Each agent's output feeds the next. |
| **parallel** | `WantedBy=` same target | All agents deploy independently, share namespace. |
| **mesh** | Full bidirectional `Wants=` | Every agent can call every other agent. |

---

## 12. Remote Kernel — Network Transparency

NFS gives you the same `open()`/`read()`/`write()` API whether the file is local or on a remote server. Helios OS gives you the same `runtime.check_tool()` whether the kernel is in-process or across the network.

### Two Backends, Same API

```python
# In-process (agent runs inside Helios OS):
kernel = Kernel.connect()  # detects local instance → _InProcessBackend
# runtime.check_tool() → direct Python call → ~0.1ms

# Remote (agent runs on separate Cloud Run):
kernel = Kernel.connect()  # no local instance → _HTTPBackend
# runtime.check_tool() → POST /api/platform/kernel/check-tool → ~50-100ms
```

The agent code is identical. The backend is selected automatically based on environment:

```bash
# If running inside Helios OS bootstrap → in-process (auto-detected)
# If running externally → set these env vars:
export FORGEOS_API_URL=https://forgeos-api.example.com
export FORGEOS_API_KEY=fos_sales_xxxx
```

### 200 Agents Across Google Cloud

```
┌────────────────────────────────────────────────────────────────────┐
│  Helios OS Control Plane (Cloud Run)                                │
│  - Kernel (policy decisions)           ◀── HTTP kernel checks ──── │
│  - Registry (200 agents)                                           │
│  - Dashboard (monitoring)                                          │
│  - Cloud SQL (budgets, audit, sessions)                           │
└────────────────────────────────────────────────────────────────────┘
                         ▲
                         │ ~50ms per check
                         │
┌────────────────────────┴───────────────────────────────────────────┐
│  Agent Fleet (5-8 Cloud Run services, grouped by department)        │
│                                                                      │
│  sales-agents (45 agents)    marketing-agents (35 agents)           │
│  finance-agents (30 agents)  operations-agents (50 agents)          │
│  support-agents (40 agents)                                         │
│                                                                      │
│  Each agent: forgeos_sdk installed, FORGEOS_API_URL set             │
│  Every tool call → HTTP POST to control plane → kernel decides      │
└────────────────────────────────────────────────────────────────────┘
```

The kernel check adds ~50-100ms per tool call. LLM calls take 5-30 seconds. The governance overhead is invisible.

---

## 13. What Helios OS Is NOT

**Helios OS is not an LLM.** It routes to Claude, GPT, Gemini, or any other model. It doesn't contain one.

**Helios OS is not a framework like LangGraph or CrewAI.** It runs ON TOP of them. Your ADK agent, CrewAI crew, LangChain chain, or OpenAI agent runs inside Helios OS. Helios OS manages the lifecycle and enforces governance — the framework handles the LLM interaction.

**Helios OS is not a chatbot.** Agents are programs that do work autonomously — researching leads, processing data, writing reports, monitoring systems. Some have a chat interface. Many don't.

**Helios OS is not Kubernetes.** Kubernetes manages containers. Helios OS manages the agent logic inside the containers. They're complementary: Kubernetes scales the infrastructure, Helios OS governs the agents.

**Helios OS is not a permissions database.** It's a runtime policy engine. Permissions are checked in real-time, on every tool call, with context-aware decisions. Not a static lookup table.

---

## 14. The Licensing Model

The entire Helios OS codebase is licensed under the **Business Source License 1.1**.

### Source-Available, Not Closed Source

Every line of code is readable. There are no binary blobs, no obfuscation, no compiled artifacts. A developer can read exactly how budget reservations, capability tokens, and the syscall pipeline work.

### Who Can Use It

| Who | Can Read? | Can Modify? | Production Use | Cost |
|---|---|---|---|---|
| Individuals (personal, non-commercial) | Yes | Yes | Yes | Free |
| Educational institutions (teaching, research) | Yes | Yes | Yes | Free |
| Companies (any commercial use) | Yes | Yes | Requires license | [Contact us](mailto:licensing@awakeventurestudio.co) |

On **2030-05-20**, the BSL auto-converts to **Apache License 2.0** and all restrictions are lifted.

### Three Tiers

| Tier | Kernel | Who Uses It |
|---|---|---|
| **Personal / Education** | Full kernel (free) | Individuals learning, teaching, academic research |
| **Self-Hosted** | Full kernel (commercial license) | Companies wanting governance on their own infra |
| **Managed** | Full kernel (hosted by us) | Companies that don't want to operate infra |

---

## Closing: The UNIX Philosophy, Applied to Agents

UNIX succeeded because it got the abstractions right: processes, files, permissions, pipes. These primitives have survived 50 years because they map to how humans think about computation.

Helios OS applies the same approach to AI agents:

- **Agents are processes** — they have identity, lifecycle, resource limits, and signals.
- **Tools are system calls** — every invocation is checked by the kernel.
- **Manifests are binaries** — they declare what an agent is and what it can do.
- **The kernel is the arbiter** — no agent touches a resource without approval.

The frameworks (ADK, CrewAI, LangChain, Anthropic SDK, OpenAI) are the compilers — they produce agents. Helios OS is the harness — it runs them safely.

```
┌──────────────────────────────────────────────────────────────┐
│                                                               │
│   "Those who do not understand UNIX                          │
│    are condemned to reinvent it, poorly."                     │
│                                        — Henry Spencer        │
│                                                               │
│   Those who do not understand operating systems               │
│   are condemned to reinvent them for AI agents.               │
│                                                               │
│   Helios OS is the harness that gets it right.               │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```
