# Checkpoints

Save and restore agent state for crash recovery and preemption. When an autonomous agent is interrupted — by a crash, a SIGTERM, or a budget eviction — it resumes from its last checkpoint instead of starting over.

## How It Works

```
Agent runs step 1 → checkpoint(step=1) → runs step 2 → checkpoint(step=2) → crash
                                                                                ↓
Agent restarts → last_checkpoint() → {step: 2} → resumes from step 2
```

## Checkpoint Data Model

```python
@dataclass
class Checkpoint:
    pid: str                    # agent process ID
    generation: int             # process generation (stale check)
    phase: str                  # phase at checkpoint time
    resource_usage: dict        # snapshot of tokens/dollars/tool_calls
    loop_progress: LoopProgress # step index, crash count, goal, custom state
    conversation_digest: str | None  # hash of conversation (future)
    created_at: str
    extra: dict                 # arbitrary metadata

@dataclass
class LoopProgress:
    step_index: int = 0
    max_iterations: int | None = None
    crash_count: int = 0
    goal: str | None = None
    last_output_summary: str | None = None
    extra: dict = field(default_factory=dict)  # agent's custom state
```

## CheckpointStore Protocol

```python
class CheckpointStore(Protocol):
    def save(checkpoint: Checkpoint) -> None
    def load(pid: str) -> Checkpoint | None
    def delete(pid: str) -> bool
    def list_all() -> list[Checkpoint]
```

Two implementations:

| Store | Backing | Use Case |
|-------|---------|----------|
| `MemoryCheckpointStore` | In-memory dict | Development, testing |
| (Future) `PostgresCheckpointStore` | PostgreSQL | Production |

## Using Checkpoints from Agent Code

### Saving

```python
from forgeos_sdk import runtime

# Save at a logical boundary
await runtime.checkpoint({
    "step": 3,
    "leads_processed": 47,
    "current_batch": ["lead-a", "lead-b"],
})
```

The `state` dict is stored in `LoopProgress.extra`. You control what goes in it — the platform doesn't interpret it.

### Restoring

```python
restored = await runtime.last_checkpoint()
if restored:
    step = restored.extra.get("step", 0)
    leads = restored.extra.get("leads_processed", 0)
    print(f"Resuming from step {step} ({leads} leads done)")
else:
    print("Fresh start — no checkpoint found")
```

### With Signal Handling

```python
# Check for signals at each step boundary
signals = await runtime.pending_signals()
if "SIGTERM" in signals:
    await runtime.checkpoint({
        "step": current_step,
        "interrupted": True,
        "reason": "SIGTERM",
    })
    return  # graceful exit, will resume later
```

## Platform-Level Checkpoints

The executor also saves checkpoints during autonomous loops:

```python
# src/platform/executor.py — _run_autonomous_loop()
self._save_checkpoint(
    agent_id,
    step_index=iteration,
    crash_count=crash_count,
    goal=agent_def.goal,
    last_output_summary=result.output[:200] if result.output else None,
)
```

And resumes from them:

```python
resume = self._resume_point(agent_id)
start_step = resume["step_index"]
crash_count = resume["crash_count"]
```

## Generation Checks

Checkpoints are invalidated when the agent's process generation changes (e.g., after a manifest update). The executor discards stale checkpoints:

```python
if checkpoint.generation != proc.identity.generation:
    self.checkpoint_store.delete(agent_id)
    return {"step_index": 0, "crash_count": 0}
```

## Source Files

- `src/platform/checkpoint.py` — Checkpoint, LoopProgress, CheckpointStore protocol, MemoryCheckpointStore
- `src/platform/executor.py` — `_save_checkpoint()`, `_resume_point()`
- `src/forgeos_sdk/runtime.py` — `checkpoint()`, `last_checkpoint()`
- `tests/test_platform_checkpoint.py` — Checkpoint store tests
- `tests/test_sdk_runtime.py` — Runtime checkpoint tests
