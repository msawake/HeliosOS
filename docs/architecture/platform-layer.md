# Platform Layer -- Technical Reference

## 1. Overview

The platform layer (`src/platform/`) provides stack-agnostic orchestration services shared by every agent regardless of its stack adapter (ForgeOS, CrewAI, ADK, OpenClaw). It sits between the stack adapters (`stacks/`) and the core/company layer (`src/core/`, `src/companies/`), owning the full agent lifecycle: registration, deployment, invocation, scheduling, eventing, LLM routing, and observability.

All components follow the same graceful-degradation principle used across ForgeOS: when an external dependency (PostgreSQL, Redis, APScheduler, Prometheus) is unavailable, the component falls back to an in-memory implementation so the platform still boots in dev/test environments.

### Component map

| Module | Class | Purpose |
|---|---|---|
| `registry.py` | `AgentRegistry` | Single source of truth for all agents across all stacks |
| `executor.py` | `PlatformExecutor` | Central dispatcher -- deploy, invoke, stop, recover |
| `scheduler.py` | `SchedulerEngine` | Cron-based and interval-based scheduled execution |
| `event_bus.py` | `EventBus` | Pub/sub event delivery and inter-agent messaging |
| `llm_router.py` | `LLMRouter` | Multi-provider LLM routing with retry and failover |
| `agentic_loop.py` | (functions) | Shared LLM-tool-use loop for all adapters |
| `audit.py` | `AuditLog` | Immutable action log with tenant isolation |
| `alerts.py` | `AlertDispatcher` | Multi-destination alert fanout (Slack, PagerDuty, log) |
| `metrics.py` | (module-level) | Prometheus gauges, counters, and histograms |

---

## 2. AgentRegistry

Maintains the canonical set of all agents. Agents are keyed by `agent_id` and queryable by stack, execution type, ownership, owner, department, and status.

### Storage model

The registry always maintains an in-memory `dict[str, AgentDefinition]`. When a `PostgresAgentRegistry` store is provided at construction, every mutation is mirrored to the database so registrations survive restarts.

### Key methods

| Method | Signature | Description |
|---|---|---|
| `register` | `(agent_def: AgentDefinition) -> str` | Add an agent. Raises `ValueError` on duplicate `agent_id`. |
| `unregister` | `(agent_id: str) -> bool` | Remove an agent and its status. Returns `False` if not found. |
| `get` | `(agent_id: str) -> AgentDefinition \| None` | Lookup by ID. Falls through to the backing store on cache miss. |
| `update` | `(agent_def: AgentDefinition) -> str` | Replace an agent definition in-place. Preserves current status. |
| `query` | `(stack?, execution_type?, ownership?, owner_id?, department?, status?) -> list[AgentDefinition]` | Filter agents by any combination of fields. All filters are optional. |
| `set_status` | `(agent_id: str, status: AgentStatus) -> None` | Update the runtime status (IDLE, RUNNING, FAILED, STOPPED, QUARANTINED, COMPLETED). |
| `get_status` | `(agent_id: str) -> AgentStatus` | Returns `STOPPED` for unknown IDs. |
| `load_from_store` | `() -> int` | Hydrate the in-memory cache from PostgreSQL. Returns count loaded. |
| `summary` | `() -> dict` | Aggregated stats: total, by_stack, by_execution_type, by_ownership, running count. |

### Constructor

```python
AgentRegistry(store=None)
```

- `store` -- Optional `PostgresAgentRegistry` instance. When `None`, the registry is purely in-memory.

---

## 3. PlatformExecutor

The central dispatcher that deploys, invokes, stops, and recovers agents across all four stacks. It wires each agent's execution type to the correct lifecycle mechanism (always-on loop, cron schedule, event subscription, on-demand reflex, or autonomous goal loop).

### Deploy flow

```
validate name --> register in DB --> scaffold files --> create in adapter --> wire execution
```

Each step after registration is wrapped in a try/except. On failure, the registration is rolled back and scaffolded files are deleted. This ordering guarantees the database never references a half-deployed agent.

1. **Validate** -- Agent name must match `^[a-zA-Z][a-zA-Z0-9_-]{1,63}$`. Path traversal characters are rejected.
2. **Register** -- Calls `registry.register(agent_def)`. Reversible via `unregister`.
3. **Scaffold** -- The stack adapter's `scaffold_files()` writes config/prompt files into `agents/{personal|shared|clients}/<name>/`.
4. **Create** -- Calls `adapter.create_agent(agent_def)` to initialize the agent in the adapter's runtime.
5. **Wire** -- Connects the agent to its execution lifecycle (see table below).

After wiring, the executor optionally validates that tools referenced in `agent_def.tools` are available. Missing tools produce a warning in metadata but do not fail the deploy.

### Execution type wiring

| Type | What `_wire_execution` does |
|---|---|
| `always_on` | Calls `adapter.start_loop(agent_id)`, sets status to RUNNING. |
| `scheduled` | Adds a job to `SchedulerEngine` with the agent's cron expression. |
| `event_driven` | Subscribes to each trigger in `agent_def.event_triggers` via `EventBus`. |
| `reflex` | Sets status to IDLE. Agent responds only to direct `invoke()` calls. |
| `autonomous` | Creates an `asyncio.Task` running `_run_autonomous_loop`. |

### Invoke flow

```python
async def invoke(agent_id, prompt, context=None, session_id=None) -> AgentResult
```

1. Look up the agent definition and its stack adapter.
2. If `session_id` is provided, acquire a per-session `asyncio.Lock` to prevent concurrent load/save races.
3. Load conversation history from the session store (if available).
4. Set status to RUNNING, call `adapter.invoke()`, set status to the result status.
5. Append the user+assistant turn to the session store and increment `turns_completed`.

### Autonomous loop and crash recovery

The autonomous loop (`_run_autonomous_loop`) invokes the agent repeatedly with iteration-tagged prompts until the goal is met, iterations are exhausted, or consecutive crashes exceed the threshold.

| Metadata key | Default | Description |
|---|---|---|
| `max_iterations` | 50 | Maximum loop iterations before stopping. |
| `loop_interval_seconds` | 30 | Sleep between iterations (seconds). |
| `restart_on_failure` | `False` | If `True`, FAILED iterations do not terminate the loop. |
| `max_crashes_before_give_up` | 3 | Consecutive unhandled exceptions before QUARANTINED status. |

Crash backoff is exponential: `min(60, 2^crash_count)` seconds. After `max_crashes_before_give_up` consecutive crashes the agent is set to `QUARANTINED` -- it will not be auto-recovered on restart and requires manual intervention.

### Boot-time recovery

```python
async def recover() -> int
```

Called after the registry is loaded from persistent storage. Re-wires execution for all agents except those in terminal states (FAILED, STOPPED) unless `restart_on_failure` is set. QUARANTINED agents are never auto-recovered. Returns the count of agents that were re-wired. Also calls `adapter.recover()` on each registered adapter for stack-specific recovery.

### Constructor

```python
PlatformExecutor(
    registry: AgentRegistry,
    scheduler: SchedulerEngine,
    event_bus: EventBus,
    agents_root: Path | str = "agents",
)
```

Adapters are registered after construction via `register_adapter(adapter)`.

---

## 4. SchedulerEngine

Manages cron-style and interval-based execution of scheduled agents. Supports two backends: APScheduler (when installed) for wall-clock-accurate cron triggers, and a built-in interval loop fallback.

### Cron expression support

| Format | Example | Backend |
|---|---|---|
| Shorthand interval | `every 15m`, `every 2h`, `every 30s` | Both |
| 5-field cron | `*/5 * * * *`, `0 8 * * 1-5` | APScheduler (exact), fallback (approximated) |
| Weekly approximation | `0 9 * * 1` | Fallback: 604800s interval |
| Daily approximation | `30 8 * * *` | Fallback: 86400s interval |

When APScheduler is available, `_build_apscheduler_trigger` converts expressions to `CronTrigger` or `IntervalTrigger` objects. The fallback parser `_parse_cron_interval_seconds` converts everything to a flat interval in seconds.

### Key methods

| Method | Signature | Description |
|---|---|---|
| `add_job` | `(agent_id, cron_expr, callback)` | Register a scheduled callback. Replaces any existing job for the same agent. |
| `remove_job` | `(agent_id) -> bool` | Cancel and remove a scheduled job. |
| `start_all` | `()` | Start the scheduler. With APScheduler, calls `AsyncIOScheduler.start()`. With fallback, spawns `asyncio.Task` per job. |
| `stop_all` | `()` | Cancel all running tasks / shut down APScheduler. |
| `list_jobs` | `() -> list[dict]` | Returns job metadata including `agent_id`, `cron_expr`, `last_run`, `next_run_at`, and `active` status. |

### Constructor

```python
SchedulerEngine(job_store=None, use_apscheduler: bool | None = None)
```

- `job_store` -- Optional `PostgresScheduledJobStore` for durability across restarts.
- `use_apscheduler` -- Force APScheduler on/off. Defaults to auto-detect based on import availability.

### Interaction with PlatformExecutor

The executor calls `scheduler.add_job(agent_id, cron_expr, callback)` during `_wire_execution` for `SCHEDULED` agents, and `scheduler.remove_job(agent_id)` during `stop_agent`.

---

## 5. EventBus

Lightweight async pub/sub event bus with an inter-agent mailbox. Agents subscribe to named events via callbacks. When an event fires, all subscribed callbacks are invoked concurrently via `asyncio.gather`.

### Event model

```python
@dataclass
class Event:
    name: str
    payload: dict[str, Any]
    timestamp: datetime
    source: str
```

### Key methods

| Method | Signature | Description |
|---|---|---|
| `subscribe` | `(event_name, agent_id, callback)` | Register a callback. Idempotent -- re-subscribing the same agent replaces its callback. |
| `unsubscribe` | `(agent_id, event_name=None) -> int` | Remove subscriptions. If `event_name` is `None`, removes all subscriptions for the agent. Returns count removed. |
| `fire` | `(event: Event) -> list[str]` | Dispatch to all subscribers concurrently. Returns list of notified `agent_id`s. Exceptions in individual callbacks are caught and logged. |
| `send_message` | `(from_agent_id, to_agent_id, content) -> str` | Queue a direct agent-to-agent message. Returns `message_id`. |
| `get_messages` | `(agent_id, unread_only=True) -> list[dict]` | Retrieve queued messages for an agent. |
| `mark_read` | `(message_id)` | Mark a message as read. |
| `get_subscriptions` | `(agent_id=None) -> dict[str, list[str]]` | Return `{event_name: [agent_ids]}`, optionally filtered. |
| `recent_events` | `(limit=50) -> list[dict]` | Return the most recent events from a bounded history buffer (max 1000). |

### Constructor

```python
EventBus(subscription_store=None, message_store=None)
```

- `subscription_store` -- Optional `PostgresEventSubscriptionStore` for persistent subscriptions.
- `message_store` -- Optional `PostgresAgentMessageStore` for durable inter-agent messaging.

### Interaction with PlatformExecutor

During `_wire_execution`, the executor subscribes `EVENT_DRIVEN` agents to each of their `event_triggers`. The callback invokes the agent with the event name and payload. During `stop_agent`, the executor calls `event_bus.unsubscribe(agent_id)`.

---

## 6. LLMRouter

Routes LLM calls to the correct provider based on the agent's `LLMConfig`. Supports Anthropic and OpenAI, with automatic detection from the model name prefix (`claude-*` goes to Anthropic, `gpt-*`/`o3-*` goes to OpenAI). When no API key is configured for a provider, returns simulated responses.

### Key methods

| Method | Signature | Description |
|---|---|---|
| `chat` | `(llm_config, messages, tools=None) -> LLMResponse` | Chat completion with retry and failover. |
| `reason` | `(llm_config, messages) -> LLMResponse` | Reasoning/thinking call using the agent's `reasoning_model` (or `chat_model` as fallback). |
| `chat_stream` | `(llm_config, messages, tools=None) -> AsyncIterator[dict]` | Streaming chat completion yielding typed events. |
| `available_providers` | `() -> list[str]` | Returns initialized provider names, or `["simulated"]`. |
| `bind_audit` | `(audit_log)` | Attach an `AuditLog` instance after construction for failover audit events. |

### Retry with exponential backoff

Every non-streaming call is wrapped with `_with_retry`. Transient errors (HTTP 429, 5xx, timeouts, rate limits) trigger up to `MAX_RETRIES` attempts with exponential backoff plus jitter. Non-retryable errors (4xx except 408/429) fail immediately.

| Env var | Default | Description |
|---|---|---|
| `FORGEOS_LLM_MAX_RETRIES` | `3` | Maximum retry attempts per call. |
| `FORGEOS_LLM_BACKOFF_BASE` | `1.0` | Base delay in seconds. |
| `FORGEOS_LLM_BACKOFF_MAX` | `30.0` | Maximum delay cap in seconds. |

### Provider failover

When the primary provider fails after all retries and `llm_config.metadata["fallback_provider"]` is set, a single failover attempt is made to the fallback provider. Failovers are recorded via the bound `AuditLog` (action `platform.llm_failover`).

If both primary and fallback fail, the returned `LLMResponse` has its `error` field set with both error messages.

### Streaming events

`chat_stream` yields events in this sequence:

```
{"type": "text_delta", "content": "..."}   -- zero or more
{"type": "done", "tokens_used": N, "text": "", "tool_calls": [...]}
```

On error: `{"type": "error", "error": "..."}`. Streaming calls do not use retry/failover -- the caller is expected to handle reconnection at the SSE layer.

### Tool format conversion

When routing to OpenAI, `_to_openai_tools` converts Anthropic-format tool definitions (`name` + `description` + `input_schema`) to OpenAI format (`type: "function"` + `function.parameters`).

### Constructor

```python
LLMRouter(api_keys: dict[str, str] | None = None, audit_log=None)
```

- `api_keys` -- Map of provider name to API key (e.g., `{"anthropic": "sk-...", "openai": "sk-..."}`).
- `audit_log` -- Optional `AuditLog` for recording failover events.

---

## 7. Agentic Loop

The shared tool-use loop used by all stack adapters. Instead of calling `llm_router.chat()` directly, adapters call `run_agentic_loop()` to get the standard LLM -> tool_use -> execute -> tool_result -> LLM cycle with built-in cost tracking, tool retry, and multi-turn history.

### `run_agentic_loop`

```python
async def run_agentic_loop(
    llm_router: LLMRouter,
    llm_config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    tool_definitions: list[dict] | None = None,
    tool_executor=None,
    agent_context: dict | None = None,
    max_turns: int = 25,
    context: dict | None = None,
    history: list[dict] | None = None,
) -> AgentResult
```

**Loop mechanics:**

1. Build the message list: system prompt, conversation history (if provided), user prompt with optional context.
2. Call `llm_router.chat()` with tool definitions.
3. If the response contains no tool calls, return the final text as a completed `AgentResult`.
4. For each tool call, execute via `_execute_tool` and append results as `tool_result` messages.
5. Repeat from step 2 until no tool calls or `max_turns` is reached.

**Cost tracking:** When the tool executor exposes a `_usage_enforcer` and `agent_context` contains `tenant_id`, the loop checks daily token limits and optional monthly cost caps before the first LLM call. Each turn records token usage and estimated cost. At completion, it records agent invocation and tool call counts.

### `run_agentic_loop_with_events`

Streaming variant that yields typed SSE events as they happen:

| Event type | Fields | When |
|---|---|---|
| `text_delta` | `content` | Each chunk of streamed LLM text |
| `tool_call` | `name`, `input` | Before executing a tool |
| `tool_result` | `name`, `result` | After executing a tool |
| `hitl_request` | `request_id`, `title`, `description`, `risk`, `category` | When `company__request_approval` tool is called |
| `done` | `tokens_used`, `text` | Loop complete |
| `error` | `error` | Unrecoverable error |

### Tool execution

```python
async def _execute_tool(tool_name, tool_input, tool_executor, agent_context,
                        *, timeout=None, max_retries=2) -> Any
```

Each tool call is wrapped with `asyncio.wait_for` for per-tool timeout enforcement and retried up to `max_retries` times on raised exceptions. Explicit error dicts returned by the tool (e.g., `{"error": "..."}`) are not retried.

| Env var | Default | Description |
|---|---|---|
| `FORGEOS_TOOL_TIMEOUT` | `60.0` | Default per-tool timeout in seconds. |
| `FORGEOS_TOOL_MAX_RETRIES` | `2` | Maximum retry attempts per tool call. |

Individual tools can override the timeout via a `timeout_seconds` key in their tool definition.

### `build_tool_definitions`

```python
def build_tool_definitions(tool_executor, agent_tools: list[str] | None = None) -> list[dict]
```

Collects tool schemas from three sources on the tool executor: custom company tools, MCP tools, and platform tools. If `agent_tools` is provided, filters to those names (supports wildcard prefixes like `"company__*"`).

---

## 8. Audit, Alerts, Metrics

### AuditLog

Records every meaningful action in the platform to an immutable `audit_log` table with tenant isolation (RLS). Falls back to a bounded in-memory ring buffer (max 1000 entries) when no database is available.

```python
AuditLog(db_client=None, tenant_id="default")
```

| Method | Signature | Description |
|---|---|---|
| `record` | `(action, *, actor, resource_type, resource_id, outcome, details) -> AuditEntry` | Append an audit entry. Writes to both memory and DB (if connected). |
| `query` | `(*, limit, resource_type?, resource_id?, action?, since?) -> list[dict]` | Query entries with optional filters. Returns newest-first. |
| `count` | `() -> int` | Total entry count (from DB or memory). |

Used by: `LLMRouter` (failover events), `PlatformExecutor` (deploy/invoke outcomes), `AlertDispatcher` (auto-trigger source).

### AlertDispatcher

Fans out alerts to multiple destinations when critical events occur. Always includes a `LogDestination` as a safety net. Additional destinations are configured via environment variables.

```python
AlertDispatcher.from_env()  # reads env vars at construction
```

| Env var | Destination |
|---|---|
| `FORGEOS_ALERT_SLACK_WEBHOOK` | `SlackDestination` -- Slack incoming webhook |
| `FORGEOS_ALERT_PAGERDUTY_KEY` | `PagerDutyDestination` -- PagerDuty Events API v2 |

| Method | Signature | Description |
|---|---|---|
| `dispatch` | `(alert: Alert) -> dict` | Send to all destinations. Returns `{destination_name: bool}`. |
| `dispatch_sync` | `(alert: Alert) -> dict` | Sync wrapper. Schedules as a task if a loop is running. |
| `from_audit_action` | `(action, *, resource_type, resource_id, details) -> dict \| None` | Auto-builds an alert if the action matches a trigger. Returns `None` if no match. |

**Auto-trigger actions and their severities:**

| Action | Severity | Meaning |
|---|---|---|
| `db.connection_lost` | SEV1 | Database connection dropped |
| `agent.crash_loop` | SEV2 | Autonomous agent hit max crashes |
| `scheduler.lag_critical` | SEV2 | Scheduler lag exceeds 10 minutes |
| `tool.crash_loop` | SEV2 | Tool crashing repeatedly |
| `platform.llm_failover` | SEV3 | LLM provider failed over |
| `cost.monthly_exceeded` | SEV3 | Tenant hit monthly cost cap |
| `approval.sla_breach` | SEV3 | HITL approval missed SLA deadline |

All dispatch is async. Alerting failures are swallowed -- they never cascade into platform failures.

### Metrics

Prometheus metrics exposed at `/metrics`. When `prometheus_client` is not installed, all metric objects are no-op shims so the rest of the codebase needs no conditionals.

Install with: `pip install -e ".[observability]"`

**Defined metrics:**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `forgeos_agents_total` | Gauge | `stack` | Registered agents per stack |
| `forgeos_agents_running` | Gauge | -- | Currently running agents |
| `forgeos_agent_deploy_total` | Counter | `stack`, `outcome` | Deploy count |
| `forgeos_agent_invoke_total` | Counter | `stack`, `outcome` | Invocation count |
| `forgeos_agent_invoke_duration_seconds` | Histogram | `stack` | Invocation duration (buckets: 0.5s -- 300s) |
| `forgeos_llm_calls_total` | Counter | `provider`, `model`, `outcome` | LLM API calls |
| `forgeos_llm_tokens_total` | Counter | `provider`, `model` | Tokens consumed |
| `forgeos_llm_failover_total` | Counter | `from_provider`, `to_provider` | Failover events |
| `forgeos_tool_calls_total` | Counter | `tool_name`, `outcome` | Tool executions |
| `forgeos_tool_duration_seconds` | Histogram | `tool_name` | Tool execution duration |
| `forgeos_scheduler_jobs_total` | Gauge | -- | Registered scheduler jobs |
| `forgeos_scheduler_lag_seconds` | Gauge | -- | Max scheduler lag |
| `forgeos_events_published_total` | Counter | `event_name` | Events published |
| `forgeos_approvals_pending` | Gauge | -- | Pending HITL approvals |
| `forgeos_approvals_resolved_total` | Counter | `outcome` | Resolved approvals |
| `forgeos_tenant_cost_usd_month` | Gauge | `tenant_id` | Month-to-date LLM cost |

Gauges are refreshed at scrape time by `refresh_platform_gauges()`, which reads current state from the registry, scheduler, and HITL system. Counters are incremented at their respective call sites.

The module uses a dedicated `CollectorRegistry` to avoid leaking default process metrics in multi-worker (gunicorn + uvicorn) deployments.
