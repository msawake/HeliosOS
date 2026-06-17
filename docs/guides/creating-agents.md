# Creating and Managing Agents in Helios OS

This guide covers everything you need to deploy, invoke, and manage agents on
the Helios OS platform. It assumes the platform is running (via `python -m
src.bootstrap` or equivalent) and you can reach the API at
`http://localhost:5001`.

---

## 1. What is an Agent?

An agent is a configured AI workload. It has a name, a runtime (stack), an
execution lifecycle, a set of tools, and a system prompt. The Helios OS framework
deploys it, manages its lifecycle, and routes its LLM and tool calls.

The key distinction: **the framework is the platform; agents are the programs.**

You do not write code to create an agent. You declare one -- a name, a stack, an
execution type, tools, and a prompt -- and the platform handles the rest:
provisioning files on disk, registering the agent in the platform registry,
wiring its execution lifecycle (cron job, event subscription, always-on loop, or
on-demand invocation), and routing every LLM call through the configured
provider.

---

## 2. Agent Definition

Every agent is an `AgentDefinition` (defined in `stacks/base.py`). The table
below describes each field.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | -- | Unique kebab-case slug. Must start with a letter, contain only `[a-zA-Z0-9_-]`, and be 2-64 characters. Examples: `lead-qualifier`, `daily-report`, `uptime-sentinel`. |
| `stack` | string | yes | -- | Runtime adapter. One of `forgeos`, `crewai`, `adk`, `openclaw`. |
| `execution_type` | enum | yes | -- | How the agent runs. One of `always_on`, `scheduled`, `event_driven`, `reflex`, `autonomous`. |
| `ownership` | enum | yes | -- | Visibility scope. One of `personal`, `shared`, `client`. |
| `agent_id` | string | no | auto (12-char UUID prefix) | Platform-assigned unique identifier. Do not set manually in most cases. |
| `owner_id` | string | no | `null` | User or client ID. Required for `personal` and `client` ownership. |
| `llm_config` | object | no | `{chat_model: "claude-4-sonnet", provider: "anthropic"}` | LLM routing config. `chat_model` determines the provider automatically: `claude-*` routes to Anthropic, `gpt-*` or `o3-*` routes to OpenAI. |
| `schedule` | string | no | `null` | Cron expression or shorthand (`"every 1h"`, `"0 9 * * *"`). Only meaningful when `execution_type` is `scheduled`. |
| `event_triggers` | list[string] | no | `[]` | Event names to subscribe to. Only meaningful when `execution_type` is `event_driven`. Examples: `["email.received", "deal.closed"]`. |
| `goal` | string | no | `null` | Objective statement for `autonomous` agents. The agent iterates until it meets this goal or exhausts its iteration budget. |
| `tools` | list[string] | no | `[]` | Allowed MCP tool names. Supports exact names and wildcards (`"company__*"`). Empty list means all available tools. |
| `system_prompt` | string | no | `""` | The system prompt injected before every LLM call. Keep it focused and specific. |
| `description` | string | no | `""` | Human-readable description. Used as a fallback system prompt if `system_prompt` is empty. |
| `department` | string | no | `""` | Organizational grouping for filtering (e.g., `sales`, `engineering`, `operations`). |
| `config_path` | string | no | auto | File path to the agent's scaffolded directory. Set by the platform during deployment. |
| `metadata` | dict | no | `{}` | Arbitrary key-value pairs. Used for tenant context, autonomous loop tuning, and custom flags. |

---

## 3. The 5 Execution Types

Execution type determines an agent's lifecycle -- how and when it runs.

### always_on

The agent starts a persistent loop immediately on deployment and stays running
until explicitly stopped. Use this for system monitors, daemons, and watchers.

**Real-world example:** An uptime sentinel that continuously checks service
health and alerts on degradation.

```yaml
# agents/shared/uptime-sentinel/config.yaml
name: "uptime-sentinel"
stack: forgeos
execution_type: always_on
ownership: shared
```

On deploy, the platform calls `adapter.start_loop(agent_id)` and sets the status
to `RUNNING`. The agent runs until you call the stop endpoint.

### scheduled

The agent runs on a cron schedule. It stays `IDLE` between runs. Use this for
daily reports, nightly batch jobs, and periodic audits.

**Real-world example:** A nightly lead scoring agent that re-ranks all leads
every evening.

```yaml
name: "nightly-lead-scoring"
stack: forgeos
execution_type: scheduled
ownership: shared
schedule: "0 21 * * *"   # 9 PM daily
```

The platform registers a cron job with the `SchedulerEngine`. At each trigger
time, it invokes the agent with a prompt containing the schedule info.

Common schedule patterns:

| Pattern | Meaning |
|---|---|
| `0 9 * * *` | Every day at 9:00 AM |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 0 1 * *` | First day of every month at midnight |
| `every 1h` | Shorthand: every hour |

### event_driven

The agent subscribes to named events on the platform's event bus. When a matching
event fires, the agent is invoked with the event payload as context.

**Real-world example:** A deal alert notifier that reacts whenever a deal is
closed or a high-value lead enters the pipeline.

```yaml
name: "deal-alert-notifier"
stack: forgeos
execution_type: event_driven
ownership: shared
event_triggers:
  - "deal.closed"
  - "lead.qualified"
```

You can subscribe to multiple event names. The platform calls
`event_bus.subscribe(trigger, agent_id, callback)` for each trigger. When the
event fires, the agent receives the prompt `"Event triggered: {event_name}"` with
the event's payload dict as context.

### reflex

The agent does nothing on its own. It sits `IDLE` and responds only when invoked
directly via the API (or from the dashboard chat). This is the simplest and most
common type for conversational agents and on-demand tools.

**Real-world example:** A research assistant that answers questions when a user
opens a chat session.

```yaml
name: "research-assistant"
stack: crewai
execution_type: reflex
ownership: shared
```

No loop, no schedule, no event subscription. The platform marks it `IDLE` and
waits for `POST /api/platform/agents/{id}/invoke` or a streaming chat request.

### autonomous

The agent runs a goal-directed loop. It is invoked repeatedly with an iteration
counter and its goal statement until (a) it reports `COMPLETED`, (b) it exhausts
its iteration budget, or (c) it is stopped externally.

**Real-world example:** A market research agent tasked with compiling a
competitive analysis report, iterating through data gathering, synthesis, and
validation until the report is complete.

```json
{
  "name": "market-expansion-planner",
  "stack": "forgeos",
  "execution_type": "autonomous",
  "ownership": "shared",
  "goal": "Identify the top 3 expansion markets and produce a ranked report with supporting data.",
  "metadata": {
    "max_iterations": 20,
    "loop_interval_seconds": 15,
    "restart_on_failure": true,
    "max_crashes_before_give_up": 3
  }
}
```

Autonomous loop metadata:

| Key | Default | Description |
|---|---|---|
| `max_iterations` | 50 | Maximum number of invoke cycles before the loop ends. |
| `loop_interval_seconds` | 30 | Seconds to sleep between iterations. |
| `restart_on_failure` | false | If true, a `FAILED` iteration does not terminate the loop. |
| `max_crashes_before_give_up` | 3 | Consecutive unhandled exceptions before the agent is quarantined. |

If the agent crashes repeatedly, it enters `QUARANTINED` status and will not
auto-recover on platform restart. Manual intervention is required.

---

## 4. The 3 Ownership Types

Ownership determines where the agent's files are scaffolded and who can access
it.

### personal

Scoped to a single user. Files are stored under `agents/personal/{owner_id}/{name}/`.
Requires `owner_id` to be set.

```json
{
  "ownership": "personal",
  "owner_id": "user-42"
}
```

### shared

Company-wide. Visible to all users in the tenant. Files are stored under
`agents/shared/{name}/`. This is the default for most production agents.

```json
{
  "ownership": "shared"
}
```

### client

Per-client isolation. Each client gets its own agent directory under
`agents/clients/{client_id}/{name}/` with isolated tool configurations. Useful
in multi-tenant scenarios where client data must not leak between agents. When
`client_id` is provided in the create request, ownership is automatically forced
to `client`.

```json
{
  "ownership": "client",
  "owner_id": "acme-corp"
}
```

---

## 5. Deploy via API

Create an agent by sending a POST to `/api/platform/agents`.

### Minimal example (reflex chat agent)

```bash
curl -X POST http://localhost:5001/api/platform/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $FORGEOS_TOKEN" \
  -d '{
    "name": "support-chatbot",
    "stack": "forgeos",
    "execution_type": "reflex",
    "ownership": "shared",
    "description": "Answers customer support questions using company knowledge base.",
    "system_prompt": "You are a helpful support agent for Acme Corp. Answer questions about billing, shipping, and returns. Be concise and accurate. If you do not know the answer, say so.",
    "chat_model": "claude-4-sonnet",
    "provider": "anthropic",
    "tools": ["knowledge__search", "knowledge__lookup"]
  }'
```

### Full example (scheduled agent with all fields)

```bash
curl -X POST http://localhost:5001/api/platform/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $FORGEOS_TOKEN" \
  -d '{
    "name": "daily-pipeline-report",
    "stack": "forgeos",
    "execution_type": "scheduled",
    "ownership": "shared",
    "department": "sales",
    "description": "Generates a daily sales pipeline summary every morning at 9 AM.",
    "system_prompt": "You are a sales analyst. Query the CRM for all open deals, calculate pipeline value by stage, and produce a markdown summary table. Include week-over-week change percentages.",
    "schedule": "0 9 * * 1-5",
    "tools": ["crm__list_deals", "crm__get_deal", "company__send_notification"],
    "chat_model": "gpt-4o",
    "provider": "openai",
    "metadata": {
      "notify_channel": "#sales-updates"
    }
  }'
```

### Response

A successful deploy returns HTTP 201:

```json
{
  "agent_id": "a1b2c3d4e5f6",
  "name": "daily-pipeline-report",
  "stack": "forgeos"
}
```

The returned `agent_id` is used for all subsequent operations (invoke, stop,
update, delete).

### Common errors

| Status | Cause |
|---|---|
| 400 | Invalid agent name, duplicate name, unknown stack, or missing required fields. |
| 401 | Missing or invalid `Authorization` header. |
| 500 | Platform executor not initialized (bootstrap issue). |

---

## 6. Deploy via Dashboard (AI Wizard)

The Helios OS dashboard includes a conversational agent creation wizard powered by
the `AgentWizardPlanner`. Instead of filling out a form, you describe what you
want in plain language.

**Flow:**

1. Open the dashboard and navigate to the agent creation page.
2. Type a natural language description of the agent you need. For example:
   "I need an agent that monitors our inbox every 15 minutes and triages support
   emails into urgent and non-urgent categories."
3. The wizard analyzes your request and produces a structured proposal, selecting
   the appropriate stack, execution type, ownership model, and tools.
4. It may ask clarifying questions if key details are ambiguous (e.g., "Should
   this agent be shared across the team or personal to you?").
5. Review the proposed configuration. The wizard displays the full agent
   definition as structured JSON.
6. Confirm to deploy. The wizard calls the same `POST /api/platform/agents`
   endpoint under the hood.

The wizard validates all fields against platform enums before allowing
deployment. Agent names are normalized to kebab-case slugs automatically.

---

## 7. Invoke an Agent

### One-shot invocation

Send a single prompt to a deployed agent. Suitable for reflex agents or
triggering an ad-hoc run of any agent.

```bash
curl -X POST http://localhost:5001/api/platform/agents/{agent_id}/invoke \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $FORGEOS_TOKEN" \
  -d '{
    "prompt": "Summarize the top 5 open deals by value.",
    "context": {}
  }'
```

Response:

```json
{
  "agent_id": "a1b2c3d4e5f6",
  "status": "completed",
  "result": "Here are the top 5 open deals by value:\n\n1. ...",
  "error": null,
  "cost_usd": 0,
  "duration": 3.214,
  "tool_calls": 2,
  "tokens_used": 1847
}
```

The `context` field is an optional dictionary passed to the agent. Event-driven
agents receive event payloads here automatically.

### Streaming chat

For multi-turn conversations, use the streaming chat endpoint. This maintains
session history so the agent sees the full conversation context.

```bash
curl -N -X POST http://localhost:5001/api/platform/agents/{agent_id}/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What deals closed this week?",
    "session_id": "session-abc123"
  }'
```

The response is a Server-Sent Events (SSE) stream. Events arrive in this order:

| Event type | Description |
|---|---|
| `session` | First event. Contains `session_id` for the conversation. |
| `text_delta` | Incremental text chunks as the LLM generates its response. |
| `tool_call` | Emitted when the agent invokes a tool. Contains tool name and input. |
| `tool_result` | The result returned by the tool. |
| `hitl_request` | Human-in-the-loop approval request (if the agent needs confirmation). |
| `done` | Final event. Contains total `tokens_used` and complete `text`. |
| `error` | Emitted if something goes wrong. Contains an `error` string. |

### Session history

Each `session_id` accumulates messages. When you send a second message with the
same `session_id`, the agent receives all prior user and assistant messages as
conversation history. This enables multi-turn dialogue without the caller needing
to manage context.

If `session_id` is omitted, the platform generates a new one (returned in the
first SSE event). Capture it and reuse it to continue the conversation.

```bash
# Start a new session (no session_id)
curl -N -X POST http://localhost:5001/api/platform/agents/{agent_id}/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, what can you do?"}'

# The first SSE event returns: {"type": "session", "session_id": "uuid-here"}
# Continue the conversation using that session_id:
curl -N -X POST http://localhost:5001/api/platform/agents/{agent_id}/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Now run that report for last quarter.", "session_id": "uuid-here"}'
```

---

## 8. Assigning Tools

Tools are MCP (Model Context Protocol) functions the agent can call during
invocation. They are specified in the `tools` field of the agent definition.

### Exact match

List specific tool names. Only these tools will be available to the agent.

```json
{
  "tools": ["crm__list_deals", "crm__get_deal", "email__send"]
}
```

### Wildcard patterns

Use a trailing `*` to match all tools with a given prefix. This is useful for
granting access to all tools from a particular MCP server or domain.

```json
{
  "tools": ["crm__*", "email__send"]
}
```

This gives the agent access to every tool whose name starts with `crm__` (e.g.,
`crm__list_deals`, `crm__create_deal`, `crm__update_deal`) plus the specific
`email__send` tool.

### Empty tools list

If `tools` is empty or omitted, the agent has access to **all** available tools.
This is convenient for prototyping but not recommended for production.

```json
{
  "tools": []
}
```

### Missing tools at deploy time

During deployment, the platform validates tool references against currently
available MCP tools. If a tool name does not match any available tool (and is not
a wildcard), a warning is logged and recorded in
`metadata._missing_tools_at_deploy`. The deployment still succeeds -- the agent
simply will not have access to those tools at invocation time.

This allows you to deploy agents before their MCP servers are connected. When
the servers come online, the tools become available automatically.

---

## 9. Lifecycle Management

### Stop an agent

Stops a running agent without removing it from the registry. Applicable to
`always_on`, `autonomous`, `scheduled`, and `event_driven` agents.

```bash
curl -X POST http://localhost:5001/api/platform/agents/{agent_id}/stop \
  -H "Authorization: Bearer $FORGEOS_TOKEN"
```

This cancels any active loop or autonomous task, removes scheduled jobs, removes
event subscriptions, and sets the status to `STOPPED`.

### Undeploy (delete) an agent

Stops the agent, removes its scaffolded files, and unregisters it from the
platform entirely.

```bash
curl -X DELETE http://localhost:5001/api/platform/agents/{agent_id} \
  -H "Authorization: Bearer $FORGEOS_TOKEN"
```

This is irreversible. The agent's directory under `agents/` is deleted.

### Update an agent

Modify an existing agent's configuration without redeploying.

```bash
curl -X PUT http://localhost:5001/api/platform/agents/{agent_id} \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $FORGEOS_TOKEN" \
  -d '{
    "name": "support-chatbot",
    "system_prompt": "Updated prompt with new instructions.",
    "tools": ["crm__*", "knowledge__*"]
  }'
```

You can update `description`, `system_prompt`, `tools`, `schedule`,
`event_triggers`, `department`, `goal`, `metadata`, and `llm_config`. The agent
continues running with the updated configuration.

### Check agent status

```bash
curl http://localhost:5001/api/platform/agents/{agent_id}/status \
  -H "Authorization: Bearer $FORGEOS_TOKEN"
```

Response:

```json
{
  "agent_id": "a1b2c3d4e5f6",
  "name": "support-chatbot",
  "stack": "forgeos",
  "execution_type": "reflex",
  "ownership": "shared",
  "status": "idle"
}
```

### Agent statuses

| Status | Meaning |
|---|---|
| `idle` | Agent is deployed and waiting for invocation. Normal state for reflex agents. |
| `running` | Agent is actively processing (loop running, invocation in progress). |
| `paused` | Agent is temporarily suspended. |
| `stopped` | Agent was explicitly stopped. Will not auto-recover on restart. |
| `completed` | Autonomous agent reached its goal. |
| `failed` | Agent encountered an error. Will not auto-recover unless `restart_on_failure` is set. |
| `quarantined` | Agent crashed repeatedly (hit `max_crashes_before_give_up`). Requires manual restart. Will never auto-recover on platform boot. |

### The QUARANTINED state

Quarantine is a safety mechanism for autonomous agents. If an agent throws
unhandled exceptions on consecutive iterations and hits the crash threshold
(default: 3), the platform sets it to `QUARANTINED` and stops the loop.

A quarantined agent will not be re-wired during platform recovery (`recover()`).
To bring it back:

1. Investigate and fix the root cause (check logs for the exception stack trace).
2. Undeploy the quarantined agent.
3. Redeploy with corrected configuration.

---

## 10. Best Practices

**Keep system prompts focused.** A good system prompt is specific about the
agent's role, tools, and output format. Avoid vague instructions like "be
helpful." Instead: "You are a deal analyst. Use the crm__list_deals tool to
query open deals. Output a markdown table with columns: Deal Name, Value, Stage,
Days Open."

**Start with reflex.** When building a new agent, deploy it as `reflex` first.
Test it interactively via the chat endpoint. Once you are confident it works
correctly, change the execution type to `scheduled`, `event_driven`, or
`autonomous` as needed.

**Test before scheduling.** Before setting a cron schedule, invoke the agent
manually with a prompt that mimics the scheduled trigger. Verify the output is
what you expect. A broken scheduled agent will run repeatedly and waste tokens.

**Use tool wildcards sparingly.** Granting `"tools": ["*"]` or broad wildcards
like `"company__*"` gives the agent access to tools it may not need. Prefer
explicit tool lists. This reduces the chance of unintended side effects and keeps
the tool definitions sent to the LLM smaller (which improves response quality
and reduces cost).

**Set goals for autonomous agents.** Without a clear `goal`, an autonomous agent
will iterate aimlessly until it hits `max_iterations`. Write a goal that has a
concrete, verifiable completion condition.

**Use metadata for tuning.** For autonomous agents, tune `max_iterations`,
`loop_interval_seconds`, and `max_crashes_before_give_up` via the `metadata`
field. For all agents, use metadata to pass tenant-specific context like
`tenant_id`, `plan`, and `monthly_limit_usd`.

**Name agents descriptively.** Use kebab-case names that describe what the agent
does: `daily-pipeline-report`, `inbox-triage`, `compliance-monitor`. This makes
the agent list self-documenting.

**Prefer shared over personal for team agents.** Personal agents are scoped to a
single user and are not visible to the rest of the team. Use `shared` ownership
for any agent that serves a team or organizational function.
