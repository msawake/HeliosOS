# ForgeOS Ubiquitous Language Glossary

A shared vocabulary for engineers, product managers, and stakeholders.
Terms are grouped by concept area, not alphabetically, so they build on each other.

---

## The Platform

### ForgeOS
The operating system for AI agents. Just like macOS doesn't care whether you run Chrome, Slack, or Photoshop — ForgeOS doesn't care what kind of AI agent runs inside it. It provides scheduling, routing, permissions, monitoring, and tooling so agent authors don't have to rebuild those things from scratch.

### Platform Layer
The shared machinery that every agent uses regardless of which runtime (Stack) it runs on. Scheduling, billing, governance, and routing all live here. Think of it as the kernel + standard library of the OS.

### Dashboard
The Next.js web UI that operators use to see running agents, approve actions, and monitor costs. The control room.

---

## Agents

### Agent
A program that runs inside ForgeOS. It has a goal, a system prompt describing its role, a set of tools it can use, and a lifecycle (how it wakes up, what triggers it, when it sleeps). An agent is to ForgeOS what a microservice is to Kubernetes — a deployable unit of work.

### Agent Definition (`AgentDefinition`)
The runtime description of an agent — its name, which Stack it runs on, what model it uses, what tools it's allowed to call, and its ownership. Think of it as the agent's process descriptor, similar to a Docker container's spec.

### Agent Manifest (`agent.yaml`)
The declarative YAML file you write to describe an agent before deploying it. Similar to a Kubernetes deployment manifest or a Docker Compose service entry. ForgeOS validates it, then creates an `AgentDefinition` from it.

### Agent ID
A short unique identifier assigned to each deployed agent instance (e.g., `a3f9c12b`). Like a Unix process ID (PID) but for agents.

### Namespace
A logical grouping of agents, like a folder or department. Agents in `sales-team` can be restricted from accessing data in `legal`. Borrowed directly from Kubernetes — same concept, different subject matter.

### Ownership Type
Who "owns" an agent:
- **Personal** — belongs to one user, only they can invoke it
- **Shared** — belongs to the whole team/company
- **Client** — scoped to a specific external client's data and context

### Agent Status
The lifecycle state an agent is in at any moment: `idle`, `running`, `paused`, `stopped`, `failed`, `completed`, `quarantined`. Quarantined means the platform has frozen it due to a policy violation.

---

## Stacks (Runtimes)

### Stack
The execution engine an agent runs on. Think of it like choosing a programming language runtime: Node.js, the JVM, and Python can all run "programs," but they do it differently. Similarly, all stacks can run agents, but they use different underlying frameworks.

ForgeOS currently has five stacks:

| Stack | Analogy | When to use |
|---|---|---|
| `forgeos` | Native binary | Default. Simple, fast, full platform access. |
| `crewai` | Docker with a specialized base image | When you want CrewAI's multi-agent crew model. |
| `adk` | Google Cloud Function | When you want Google ADK's runner and extensions. |
| `openclaw` | External subprocess via HTTP | When the agent needs its own persistent workspace (like a Claude Code session). |
| `sandbox` | Isolated Docker container | When you need hard resource limits and filesystem isolation. |

### Stack Adapter
The code that bridges a specific stack to the platform. Every adapter implements the same interface (`create_agent`, `invoke`, `start_loop`, `stop`) so the platform treats all stacks identically. Like a USB-C adapter — the port is always the same, the device behind it is different.

### Fallback
When a stack's preferred execution method is unavailable (e.g., Docker is not running for the `sandbox` stack), it falls back to the platform's own in-process agentic loop. The agent still runs, just without isolation.

---

## Execution Types (Lifecycles)

How and when an agent wakes up:

### Reflex
Fires immediately in response to a direct API call. Like a function call — invoke it, get a result, done. No persistent state between invocations.

### Scheduled
Wakes up on a cron schedule (e.g., "every morning at 8am"). Like a cron job. Uses the **Scheduler Engine** internally.

### Event-Driven
Wakes up when a named event is published to the **Event Bus** (e.g., `lead.created`, `invoice.overdue`). Like a webhook listener or an SNS subscriber.

### Always-On
Runs in a persistent loop, continuously processing work. Like a long-running server process or a Kubernetes `Deployment` with 1 replica always alive.

### Autonomous
Pursues a high-level goal across multiple turns without step-by-step human instructions. Closest to "set it and forget it." Uses the agentic loop internally but with broader latitude.

---

## The Execution Engine

### Agentic Loop
The core reasoning cycle of an agent: **Think → Act → Observe → repeat**. Concretely:
1. Send the current context to the LLM → get a response
2. If the LLM wants to use a tool → execute it
3. Feed the tool result back to the LLM
4. Repeat until the LLM gives a final answer (no more tool calls)

This is what makes an agent different from a simple chatbot. A chatbot just replies. An agent acts.

### LLM Router
The dispatcher that sends LLM requests to the right model and provider. An agent might be configured to use `claude-sonnet-4-6` via Anthropic for chat and `o3` via OpenAI for reasoning. The router handles retries, failover, and rate limiting transparently. Think of it as a load balancer in front of your AI models.

### Platform Executor (`PlatformExecutor`)
The process manager of ForgeOS. It deploys agents (registers them), invokes them, runs scheduled/event-driven lifecycles, and tracks each agent's process record. What `systemd` is to Linux processes, the Executor is to ForgeOS agents.

### Agent Registry
The directory of all deployed agents. You can query it by stack, namespace, owner, or department. Like a DNS server — you ask "who handles `sales-team/lead-scorer`?" and the registry tells you the agent ID.

### Scheduler Engine
Manages cron-style scheduling. When a `scheduled` agent's time comes, the Scheduler fires it. Backed by `APScheduler` when installed, otherwise uses a simple polling loop.

### Event Bus
A pub/sub message system inside the platform. Components publish events (`lead.scored`, `payment.failed`), and subscribed agents wake up to handle them. Like Kafka or SNS, but embedded in-process unless you wire up a real broker.

---

## Governance & Safety

### Kernel
The policy enforcement point — the "bouncer" of ForgeOS. Before any significant action (tool call, A2A delegation, secret access, budget spend), the Kernel decides: allowed or denied? It checks identity, capabilities, quotas, and data boundaries in sequence.

### Syscall
Borrowing from operating systems: when a program needs to do something privileged (access the filesystem, open a network socket), it makes a *system call* to the OS kernel. In ForgeOS, when an agent wants to call a tool or delegate to another agent, it makes a **syscall** to the Kernel. The Kernel validates and either permits or denies it.

The new syscall pipeline (`FORGEOS_SYSCALL_PIPELINE=1`) runs **7 stages** in order:
1. **Identity** — who is this agent?
2. **Capability** — does it hold a token granting this action?
3. **Quota/Budget** — can it afford this? (token and USD limits)
4. **Policy** — does the hook chain allow it?
5. **Boundary** — is the data it's accessing within its namespace?
6. **Dispatch** — execute the action
7. **Audit** — record what happened

### Hook Chain (Legacy)
The original 7-check governance system (budget, rate limit, auth, cost, compliance, Slack, audit). Still runs by default. Being migrated to the Syscall pipeline. Think of it as the old version of the Kernel's admission logic.

### Capability
A short-lived permission token the Kernel can grant to an agent. Like a VIP wristband at an event — it proves you're allowed in without needing to check every policy every time. Has an expiry and can be revoked.

### HITL (Human-in-the-Loop)
A hard gate where the agent **must** pause and wait for a human to approve before proceeding. Used for high-risk actions: sending an email to a client, executing a financial transaction, making a legal commitment. The agent literally stops and enqueues an approval request.

### Audit Log
An append-only, hash-chained record of every significant platform event. Hash-chained means each entry includes a hash of the previous one — tampering with any past record would break the chain and be detectable. Like a blockchain ledger, but for agent activity.

### Operating Mode
How much autonomy agents have globally:
- **Shadow** — agents reason and prepare actions but never execute them
- **Supervised** — agents execute but checkpoint risky actions for review
- **Autonomous** — agents execute independently within policy limits

---

## Tooling

### Tool
A function an agent can call during its agentic loop. Examples: `search_web`, `send_email`, `query_crm`, `read_file`. The LLM asks to use a tool; the platform executes it; the result goes back to the LLM.

### MCP (Model Context Protocol)
An open standard for connecting AI agents to external tools and data sources. Think of it as USB for AI — a standardized plug that lets any agent connect to any tool server without custom integration code. ForgeOS supports running and managing MCP servers.

### MCP Server
A process that exposes tools via the MCP protocol. ForgeOS boots, discovers, and manages the lifecycle of these servers. An agent configured to use `mcp__crm__search_contact` is calling a tool served by a CRM MCP server.

### Tool Executor
The component that receives a tool call from the agentic loop and routes it to the right handler: either an MCP server (`mcp__*` prefix) or an in-process company handler (`company__*` prefix). It also checks the Kernel gate before executing.

### Skill
A pre-built domain expertise module. Not a live tool — rather a chunk of structured knowledge or a reusable prompt fragment that grounds an agent's reasoning in a specific domain (e.g., "BANT lead scoring," "GDPR compliance rules"). There are 230+ skills in the library.

---

## Agent-to-Agent (A2A)

### A2A (Agent-to-Agent Protocol)
The way agents delegate tasks to other agents. Like a microservice calling another microservice, but with permission checks and cycle detection built in. An executive agent can call a worker agent via `agent__call(namespace, name, task)`.

### Delegation Chain
The call stack of A2A invocations. If Agent A calls Agent B which calls Agent C, the chain is [A → B → C]. The platform tracks this to enforce depth limits and detect cycles (Agent A accidentally calling itself through a chain).

### A2H (Agent-to-Human)
The protocol for an agent to interact with a human mid-task. Tools like `human__ask`, `human__notify`, and `human__check` let an agent request input or approval from a specific user without stopping the entire workflow.

---

## Persistence & State

### Persistence Layer
The storage backend for registries and stores. When a `DATABASE_URL` is set, it uses PostgreSQL. Otherwise everything lives in-memory and is lost on restart. Graceful degradation by design.

### Process Table (`AgentProcess`)
A record tracking each agent invocation: its stable ID (PID), lifecycle phase, resource usage (tokens, USD, tool calls, wall clock time). Like `/proc` in Linux — a live view of what each agent is consuming.

### Checkpoint
A snapshot of an agent's execution state that allows it to be paused and resumed later — even after a restart. Used for preemption (stopping a long-running agent to free resources) and durable resume after a crash.

### Session Store
Keeps the conversation history (prior turns) for agents that support multi-turn chat. Currently always in-memory.

---

## Infrastructure

### Multi-Tenancy
Multiple companies (tenants) share the same ForgeOS installation, but their data is strictly isolated. Every database table has a `tenant_id` column, and PostgreSQL Row-Level Security (RLS) enforces that queries only return the current tenant's rows.

### Tenant
One company or customer using ForgeOS. Their agents, data, and usage are isolated from all other tenants.

### Company Pack
A bundled set of pre-configured agents, workflows, and knowledge for a specific industry vertical. ForgeOS ships five example packs: LeadForge AI (B2B sales), DealForge AI (M&A), TravelForge AI, InsureForge AI, HomeForge AI. These are example workloads, not the platform itself.

### Bootstrap
The startup sequence that initializes every platform subsystem in order: load env, connect to DB (or fall back to in-memory), register stack adapters, build the executor, start the FastAPI server. Running `python -m src.bootstrap` is how you start ForgeOS.
