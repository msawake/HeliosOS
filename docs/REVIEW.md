# ForgeOS Codebase Review — May 2026

## Verdict: Is This a Real Harness?

**The architecture is real. The wiring is 70% complete. Some critical paths are scaffolding.**

ForgeOS has genuine OS-level primitives that no other framework has (capability tokens, two-phase budget reservation, signal delivery, syscall pipeline). The kernel, process table, and adapter interception patterns are well-designed. But several advertised features are declared in schema/docs but not enforced in code.

---

## What Works (Production-Ready)

| Component | Evidence |
|---|---|
| Tool ACLs (whitelist/denylist with wildcards) | PermissionManager tested, fires on every tool call |
| A2A delegation (depth limits, cycle detection, capability tokens) | A2AHandler tested, isolation policy works |
| Process lifecycle (9-phase state machine, cascading) | ProcessTable fully implemented |
| Checkpoint/resume for autonomous agents | Saves/loads during autonomous loop |
| 6 real framework adapters (ADK, CrewAI, Claude SDK, OpenAI, OpenClaw, Sandbox) | Kernel gate fires correctly in each |
| In-process (~0.1ms) and HTTP (~50ms) kernel modes | _InProcessBackend and _HTTPBackend both work |
| Team manifest deployment | deploy_team with supervisor/sequential/parallel/mesh |
| Budget tracking (daily limits enforced) | BudgetManager with thread-safe locking |
| Manifest schema (v1 + v2 AgentOS) | Comprehensive Pydantic validation |
| Kernel fallback (Community Edition stubs) | Imports stubs when kernel removed |

## What's Advertised But Not Wired

| Feature | Status | What's Missing |
|---|---|---|
| Callback governance (GUIDE steering) | Code exists, never activated | callback_registry never passed to agentic loop by any adapter |
| PII masking/redaction | get_pii_policy() returns policy | No enforcement — nothing masks data |
| Budget two-phase reservation | reserve/commit/release exist | Executor never calls them during tool execution |
| Governance policies from manifests | Schema accepts policies | Executor doesn't read governance fields |
| Data namespace boundaries | Schema accepts allowed/blocked | Executor doesn't check DataBoundaryManager |
| Capability tokens for tool/data | Only checked during A2A | PermissionManager.check_tool_call doesn't check capabilities |
| Conversation digest validation | Stored in checkpoint | Never compared on resume |

## Critical Bugs

1. **Syscall A2A parameter mismatch** — `_syscall.py` uses `callee_namespace` but PermissionManager expects `target_namespace`. Will TypeError at runtime.
2. **Signal reason lost** — Dead code line reads value and discards it.
3. **PolicyEngine not thread-safe** — `_policies` dict accessed without lock.

## Adapter Status

| Adapter | Rating | Notes |
|---|---|---|
| ForgeOS native | **REAL** | Missing callback_registry pass |
| Google ADK | **REAL** | Full SDK + fallback |
| CrewAI | **REAL** | Full SDK + fallback |
| Anthropic Agent SDK | **REAL** | PreToolUse hook + fallback |
| OpenAI Agents | **PARTIAL** | SDK works, some paths untested |
| OpenClaw | **REAL** | ToolProxyServer + fallback |
| Sandbox | **REAL** | Docker + token auth + fallback |
| LangChain | **STUB** | Always falls back to platform loop |
| Anthropic Managed | **STUB** | HTTP client skeleton only |

## Test Coverage Gaps

12 platform modules have zero tests: audit, triggers, agentic_loop, persistence, memory_store, namespace_policy, rbac, task_queue, postgres_process_table, session_event_store, fleet_monitor, and conversation_manager.

No integration test validates: manifest → deploy → invoke → kernel enforcement.

## Priority Fix Roadmap

| Phase | Effort | What |
|---|---|---|
| **0: Critical** | 1-2 days | Fix syscall bug, wire callbacks, enforce governance from manifests |
| **1: Complete** | 1 week | Wire budget two-phase, capability tokens for tools, PII enforcement |
| **2: Adapters** | 1 week | LangChain real integration, Anthropic Managed, deduplicate gate pattern |
| **3: Tests** | 1 week | Integration test, 12 untested modules, team A2A enforcement |
| **4: Honesty** | 2 days | Emit warnings for unimplemented manifest fields |
