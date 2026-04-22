# Syscall Pipeline

The single admission path for agent actions. Every meaningful operation — tool call, A2A invocation, secret fetch, budget reservation, data read — becomes a `Syscall` that traverses one ordered pipeline.

## Pipeline Stages

```
identity → capability → quota/budget → policy → boundary → dispatch → audit
```

Each stage returns `None` (continue) or a `KernelDecision` (short-circuit). The pipeline stops on the first non-allow decision.

| Stage | Responsibility | Can Emit | Backed By |
|-------|---------------|----------|-----------|
| **identity** | Resolve caller PID to agent record | continue | ProcessTable |
| **capability** | Check tool/A2A/data ACLs | `deny` | PermissionManager |
| **quota** | Two-phase budget reservation | `rate_limit` | BudgetManager |
| **policy** | Evaluate declarative JSON-logic rules | `deny` | PolicyEngine |
| **boundary** | Namespace isolation + PII masking | `mask` | DataBoundaryManager |
| **dispatch** | Execute the actual operation | result | Caller-supplied |
| **audit** | Hash-chained append of decision record | always runs | AuditRecorder |

## Syscall Record

Every action is encoded as a `Syscall` dataclass that travels through all stages:

```python
@dataclass
class Syscall:
    verb: str           # "tool.call", "a2a.invoke", "secret.get", "data.read"
    subject: str        # caller PID (agent_id)
    object: str         # target (tool name, callee PID, secret key)
    args: dict          # concrete arguments (tool_input, estimated_cost_usd, ...)
    context: dict       # scratchpad shared across stages (tenant_id, ...)
    budget_ticket: str   # set by quota stage, consumed by dispatch
    issued_at: str      # ISO timestamp
```

## 5 Possible Outcomes

| Decision | Meaning | Which stages emit it |
|----------|---------|---------------------|
| `allow` | Proceed with execution | All stages (default) |
| `deny` | Action blocked | capability, policy |
| `rate_limit` | Budget exceeded | quota |
| `mask` | Partial access, data redacted | boundary |
| `ask_human` | Human-in-the-loop required | policy (via governance rules) |

## Using the Pipeline

### Via SDK Runtime (recommended)

```python
from forgeos_sdk import runtime

decision = await runtime.syscall("tool.call", target="email.send")
if decision.allowed:
    # proceed
```

### Via Kernel Directly

```python
decision = kernel.syscall(
    verb="tool.call",
    subject=agent_id,
    object="email.send",
    args={"tool_input": {"to": "user@example.com"}},
    dispatcher=my_dispatch_function,
)
```

### Via High-Level Checks

The kernel's composite methods (`check_tool_call`, `check_a2a_call`, `check_data_access`) run the same subsystems but without the full pipeline stages:

```python
decision = kernel.check_tool_call(agent_id, "email.send", tool_input, estimated_cost_usd=0.05)
```

## Feature Flag

The syscall pipeline is **on by default**. Set `FORGEOS_SYSCALL_PIPELINE=0` to fall back to the legacy `src/core/hooks.py` chain during the migration period.

```python
from src.platform.syscall import syscall_pipeline_enabled

if syscall_pipeline_enabled():
    # new path: kernel.syscall(...)
else:
    # legacy path: hooks.pre_tool_use(...)
```

## How Stages Are Wired

The kernel builds the pipeline lazily on first `syscall()` call:

```python
# src/platform/kernel.py — Kernel._build_pipeline()
SyscallPipeline(stages={
    "capability": make_capability_stage(self.permissions),
    "quota":      make_quota_stage(self.budgets),
    "policy":     make_policy_stage(self.policies),
    "boundary":   make_boundary_stage(self.data),
    "audit":      make_audit_stage(self._audit_log),
})
```

The `dispatch` stage is caller-supplied per syscall — it's the function that actually executes the operation (tool call, A2A invoke, etc.).

## Stage Implementation

Each stage is a callable conforming to the `Stage` protocol:

```python
@runtime_checkable
class Stage(Protocol):
    def __call__(self, syscall: Syscall) -> KernelDecision | None: ...
```

Return `None` to continue. Return a `KernelDecision` to short-circuit. Stages must never raise for policy decisions — exceptions are caught by the pipeline runner and converted to `deny(reason="stage crashed")`.

## Error Handling

- Stage crashes → `deny(reason="stage 'X' crashed: ...")` — never silent failures
- Audit stage runs even on denial (via `_run_audit_on_deny`)
- Budget tickets are attached to the syscall object so dispatch can commit/release after

## Relationship to hooks.py

The legacy `src/core/hooks.py` chain (6 hooks: budget, rate limit, auth, compliance, audit, slack) is deprecated. The syscall pipeline replaces it with a unified, ordered, typed admission path. During migration:

- Both paths coexist
- Feature flag controls which runs
- New code must use the syscall pipeline
- Target end-state: delete `hooks.py`

## Source Files

- `src/platform/syscall.py` — Syscall, SyscallPipeline, Stage protocol, stage factories, feature flag
- `src/platform/kernel.py` — `Kernel.syscall()`, `_build_pipeline()`
- `tests/test_platform_syscall.py` — Pipeline stage tests, feature flag tests
- `tests/test_tool_executor_syscall_adoption.py` — Integration with tool executor
