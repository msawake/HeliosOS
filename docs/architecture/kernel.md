# The AgentOS Kernel

The kernel is the **policy decision point** for every meaningful agent action. It validates contracts at deploy time and enforces permissions, budgets, data boundaries, and policies at runtime.

Think of it like Linux Security Modules (LSM) + cgroups + SELinux, but for AI agents instead of processes.

## Six Kernel Subsystems

The kernel (`src/platform/kernel.py`) composes six specialized managers behind a single facade:

| Subsystem | Responsibility | Classical OS analog |
|-----------|---------------|---------------------|
| `AdmissionController` | Validates contracts before deploy | `execve()` + ELF loader |
| `PermissionManager` | Tool call + A2A ACL checks | LSM hooks |
| `BudgetManager` | Per-task + daily USD budget enforcement | cgroups quotas |
| `PolicyEngine` | Evaluates declarative rules from manifest | SELinux policies |
| `DataBoundaryManager` | Namespace allow/block + PII policy | Linux namespaces |
| `AuditRecorder` | Records every decision | auditd |

## Two Phases — Admission and Runtime

### Admission (once, at deploy)

```
parse manifest
  -> validate schema (Pydantic)
  -> check signature (if required)
  -> check (namespace, name) uniqueness
  -> verify dependencies exist
  -> check tool availability (warn if missing)
  -> check lifecycle consistency
  -> evaluate admission policies
  -> assign uid + generation
  -> approve or reject
```

### Runtime (many, on every action)

```
agent_context + requested_action
  -> permission check (whitelist + deny list)
  -> budget check (tokens + USD)
  -> policy evaluation (declared rules)
  -> data boundary check (namespace)
  -> record audit
  -> return KernelDecision
```

## The Core Primitive: `KernelDecision`

Every kernel check returns a single uniform type:

```python
@dataclass
class KernelDecision:
    action: Literal["allow", "deny", "mask", "ask_human", "rate_limit"]
    reason: str
    details: dict
    audit_id: str
    timestamp: str

    @property
    def allowed(self) -> bool: ...
    @property
    def denied(self) -> bool: ...
    @property
    def needs_human(self) -> bool: ...
```

This unifies what was previously scattered across `HookDecision`, tool whitelist errors, A2A errors, and budget exceptions.

## The Composite Flow

The kernel's composite `check_tool_call()` runs checks in order, short-circuiting on first denial:

```
check_tool_call(agent_id, tool_name, tool_input, estimated_cost_usd)
  |
  +-- 1. PermissionManager: tool whitelist + deny list
  |       (denies immediately if blocked)
  |
  +-- 2. BudgetManager: per-task + daily USD (if cost estimate provided)
  |       (denies if over limit)
  |
  +-- 3. PolicyEngine: evaluates all spec.governance.policies refs
  |       (denies if any policy matches a deny rule)
  |
  +-- 4. AuditRecorder: records the decision
  |
  +-- returns KernelDecision
```

## Usage from Agent Code

Agents deployed with the SDK can call the kernel to check permissions before acting:

```python
from forgeos_sdk import Kernel

# In-process (ForgeOS native stack)
kernel = Kernel.local()

# Remote (CrewAI containers, external agents)
kernel = Kernel.remote("http://forgeos:5000", api_key="...")

# Auto-detect
kernel = Kernel.connect()

# Unified async API in both modes
decision = await kernel.check_tool_call(
    agent_id="my-agent-id",
    tool_name="email.send",
    tool_input={"to": "customer@example.com"},
    estimated_cost_usd=0.02,
)

if decision.denied:
    raise PermissionError(decision.reason)
if decision.needs_human:
    # Wait for HITL approval...
    pass

# Introspection
contract = await kernel.get_contract("my-agent-id")
print(contract["spec"]["boundaries"]["budgets"])

# Audit custom events
await kernel.audit("my-agent-id", "decision_made", {"choice": "approved"})
```

## Architecture

```
                    +-------------------+
                    |   Agent (user)    |
                    +---------+---------+
                              |
             +----------------+----------------+
             |  forgeos_sdk.Kernel (client)    |
             |   local()   |   remote()        |
             +------+----------------+---------+
                    |                |
            in-process            HTTP
                    |                |
                    v                v
             +----------------------------+
             |  platform.Kernel (facade)  |
             +------+------+------+-------+
                    |      |      |
             +------+--+--+-+--+--+--+
             | Admission| Permissions| Budget   |
             +----------+------------+----------+
             | Policy   | Data       | Audit    |
             +----------+------------+----------+
```

## HTTP API

The kernel is exposed over HTTP for remote SDK clients and external agents:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/platform/kernel/check-tool` | Tool call permission check |
| POST | `/api/platform/kernel/check-a2a` | Agent-to-agent permission check |
| POST | `/api/platform/kernel/check-data` | Data namespace access check |
| GET  | `/api/platform/kernel/contract/{agent_id}` | Retrieve full contract |
| POST | `/api/platform/kernel/admit` | Validate contract before deploy |
| POST | `/api/platform/kernel/audit` | Record custom audit event |

All endpoints return `KernelDecision` (as JSON) or `AdmissionResult`.

## Policy Engine

The `PolicyEngine` evaluates declarative rules from the manifest's `spec.governance.policies`. Today it supports a JSON-logic subset — OPA/Rego integration is a future upgrade.

Supported operators:

| Op | Example |
|----|---------|
| `equals` | `{"op": "equals", "field": "agent_namespace", "value": "admin"}` |
| `contains` | `{"op": "contains", "field": "tool_name", "value": "shell"}` |
| `gt` | `{"op": "gt", "field": "estimated_cost_usd", "value": 5.00}` |
| `in` | `{"op": "in", "field": "tool_name", "value": ["delete", "drop"]}` |

Policies are referenced in the manifest by name:

```yaml
spec:
  governance:
    policies:
      - name: no-shell-tools
        ref: policies/no-shell.json
      - name: no-pii-egress
        ref: policies/pii-egress.rego
```

The kernel loads policy files at startup and evaluates each referenced policy at tool-call time.

## Admission Policies

At admission, the kernel validates:

1. **Name format** — alphanumeric, hyphens, underscores; starts with letter; 2-64 chars
2. **Stack** — must be one of `forgeos`, `crewai`, `adk`, `openclaw`, `langgraph`
3. **Namespace** — same format rules as name; scoped uniqueness
4. **Uniqueness** — `(namespace, name)` must not already exist
5. **Lifecycle consistency** — `scheduled` requires `schedule`, `event_driven` requires `event_triggers`
6. **Dependencies** — declared agent deps must exist (unless `optional: true`)
7. **Tool availability** — warns (does not block) if tools don't resolve

Returns `AdmissionResult` with separate `errors` (blocking) and `warnings` (non-blocking).

## Testing the Kernel

```bash
PYTHONPATH=. python3 -m pytest tests/test_kernel.py -v
```

24 tests cover every subsystem, composite flow, and SDK round-trip.
