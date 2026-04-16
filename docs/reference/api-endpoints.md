# ForgeOS Platform API Reference

Complete reference for all FastAPI endpoints defined in `src/dashboard/fastapi_app.py`.

Base URL: `http://localhost:8000` (default) or the port configured via `--port`.

Authentication: Endpoints marked **Auth: Required** expect an `X-API-Key` header.
Public paths (`/api/health`, `/api/readiness`, `/api/auth/token`, `/api/me`, `/docs`,
`/redoc`, `/openapi.json`, all `/api/approvals/*` paths, `/api/admin/chat`, and
`/api/intelligence/ask`) skip auth checks entirely.

---

## 1. Health and System

### GET /api/health

System health check. Returns component status, registered agent count, pending
approvals, pending events, and available LLM providers.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "status": "ok",
  "components": {
    "database": true,
    "llm_providers": ["anthropic", "openai"],
    "adapters": ["forgeos", "crewai"],
    "agents_registered": 41,
    "pending_approvals": 3,
    "pending_events": 0,
    "timestamp": "2026-04-12T14:30:00+00:00"
  }
}
```

### GET /api/readiness

Kubernetes readiness probe. Returns 503 until the platform has finished booting.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "ready": true
}
```

### GET /metrics

Prometheus scrape endpoint. Refreshes snapshot gauges (agent counts, scheduler lag,
pending approvals) before emitting counters and gauges in Prometheus exposition format.

**Auth:** Not required
**Request body:** None
**Response:** Plain text in Prometheus exposition format (`text/plain`).

---

## 2. Agents

### GET /api/platform/overview

Platform agent registry summary with counts by stack, execution type, and status.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "total": 41,
  "running": 5,
  "by_stack": { "forgeos": 30, "crewai": 11 },
  "by_execution_type": { "always_on": 3, "scheduled": 10, "event_driven": 28 }
}
```

### GET /api/platform/agents

List all registered agents with optional query filters.

**Auth:** Not required
**Query parameters:**
| Parameter        | Type   | Description                          |
|------------------|--------|--------------------------------------|
| `stack`          | string | Filter by stack adapter name         |
| `execution_type` | string | Filter by execution type             |
| `ownership`      | string | Filter by ownership type             |
| `owner_id`       | string | Filter by owner ID                   |
| `department`     | string | Filter by department                 |
| `client_id`      | string | Filter by client (sets ownership=client) |

**Request body:** None
**Response:**
```json
[
  {
    "agent_id": "sales-lead-gen",
    "name": "Lead Generator",
    "stack": "forgeos",
    "execution_type": "event_driven",
    "ownership": "shared",
    "department": "sales",
    "description": "Generates qualified leads",
    "status": "idle"
  }
]
```

### GET /api/platform/agents/{agent_id}

Get a single agent's full detail by ID.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "agent_id": "sales-lead-gen",
  "name": "Lead Generator",
  "stack": "forgeos",
  "execution_type": "event_driven",
  "ownership": "shared",
  "department": "sales",
  "description": "Generates qualified leads",
  "goal": "Find and qualify new leads",
  "tools": ["crm_search", "email_send"],
  "schedule": null,
  "event_triggers": ["new_signup"],
  "system_prompt": "You are a lead generation specialist...",
  "llm_config": { "chat_model": "gpt-4o", "provider": "openai" }
}
```

### POST /api/platform/agents

Deploy a new agent. Returns 201 on success.

**Auth:** Required
**Request body:**
```json
{
  "name": "My Agent",
  "stack": "forgeos",
  "execution_type": "event_driven",
  "ownership": "shared",
  "owner_id": "",
  "department": "sales",
  "description": "Agent description",
  "goal": "Agent goal",
  "schedule": null,
  "event_triggers": ["new_signup"],
  "tools": ["crm_search"],
  "metadata": {},
  "chat_model": "gpt-4o",
  "provider": "openai",
  "client_id": null,
  "system_prompt": "You are..."
}
```
**Response (201):**
```json
{
  "agent_id": "generated-uuid",
  "name": "My Agent",
  "stack": "forgeos"
}
```

### PUT /api/platform/agents/{agent_id}

Update an existing agent's configuration in-place. Accepts the same body schema as
create. Only non-empty/non-default fields are applied. If `execution_type` changes
the agent is stopped, re-wired, and restarted under the new type.

**Auth:** Required
**Request body:** Same as `POST /api/platform/agents` (AgentCreateRequest).
**Response:** The updated agent definition (same shape as GET agent detail).

### POST /api/platform/agents/{agent_id}/invoke

Invoke an agent with a one-shot prompt. Tries the platform executor first (multi-stack
agents), then falls back to the legacy admin invoker (company agents from config).

**Auth:** Required
**Request body:**
```json
{
  "prompt": "Find leads in the healthcare vertical",
  "context": {}
}
```
**Response:**
```json
{
  "agent_id": "sales-lead-gen",
  "status": "completed",
  "result": "Found 12 qualified leads...",
  "error": null,
  "cost_usd": 0,
  "duration": 3.2,
  "tool_calls": 4,
  "tokens_used": 1520
}
```

### POST /api/platform/agents/{agent_id}/chat/stream

Multi-turn streaming chat with an agent. Creates or resumes a conversation session.
Returns an SSE stream with event types: `session`, `text_delta`, `tool_call`,
`tool_result`, `hitl_request`, `done`, `error`.

**Auth:** Not required
**Request body:**
```json
{
  "message": "What leads came in today?",
  "session_id": null
}
```
**Response:** `text/event-stream` (SSE). Each event is a JSON line:
```
data: {"type": "session", "session_id": "abc-123"}

data: {"type": "text_delta", "content": "Here are today's "}

data: {"type": "tool_call", "name": "crm_search", "input": {...}}

data: {"type": "tool_result", "name": "crm_search", "output": "..."}

data: {"type": "done", "tokens_used": 850, "text": "Here are today's leads..."}
```

### GET /api/platform/agents/{agent_id}/chat/sessions

List all chat sessions for an agent, sorted by creation time descending.

**Auth:** Not required
**Request body:** None
**Response:**
```json
[
  {
    "session_id": "abc-123",
    "created_at": "2026-04-12T14:30:00+00:00",
    "message_count": 6,
    "preview": "What leads came in today?"
  }
]
```

### GET /api/platform/agents/{agent_id}/chat/history

Get the full message history for a specific chat session.

**Auth:** Not required
**Query parameters:**
| Parameter    | Type   | Required | Description  |
|--------------|--------|----------|--------------|
| `session_id` | string | Yes      | Session ID   |

**Request body:** None
**Response:**
```json
{
  "session_id": "abc-123",
  "agent_id": "sales-lead-gen",
  "messages": [
    { "role": "user", "content": "What leads came in today?" },
    { "role": "assistant", "content": "Here are today's leads..." }
  ],
  "created_at": "2026-04-12T14:30:00+00:00"
}
```

### DELETE /api/platform/agents/{agent_id}/chat/sessions/{session_id}

Delete a chat session and its message history.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{ "ok": true }
```

### POST /api/platform/agents/{agent_id}/stop

Stop a running agent.

**Auth:** Required
**Request body:** None
**Response:**
```json
{ "ok": true }
```

### DELETE /api/platform/agents/{agent_id}

Undeploy and permanently delete an agent.

**Auth:** Required
**Request body:** None
**Response:**
```json
{ "ok": true }
```

---

## 3. Agent Wizard

### POST /api/platform/wizard/chat

AI-assisted agent design via a conversational wizard. Accepts the full conversation
history and optional context. May return a deploy proposal when the agent design is
complete.

**Auth:** Not required
**Request body (raw JSON, not Pydantic):**
```json
{
  "messages": [
    { "role": "user", "content": "I need an agent that monitors Slack for support tickets" },
    { "role": "assistant", "content": "Great idea..." },
    { "role": "user", "content": "It should auto-triage based on severity" }
  ],
  "context": {}
}
```
**Response:** Varies depending on conversation state. May include a `proposal` field
with a full agent definition ready for deployment:
```json
{
  "reply": "Here is the proposed agent...",
  "proposal": {
    "name": "Support Triage Agent",
    "stack": "forgeos",
    "execution_type": "event_driven",
    "tools": ["slack_read", "jira_create"],
    "system_prompt": "..."
  }
}
```

---

## 4. Events

### GET /api/events

Query the event bus for published events.

**Auth:** Not required
**Query parameters:**
| Parameter    | Type   | Description                  |
|--------------|--------|------------------------------|
| `department` | string | Filter by target department  |
| `status`     | string | Filter by event status       |
| `priority`   | string | Filter by priority (e.g. HIGH) |

**Request body:** None
**Response:** Array of event objects (max 100):
```json
[
  {
    "event_id": "evt-001",
    "event_type": "new_lead",
    "source_agent": "web-scraper",
    "target_department": "sales",
    "category": "NOTIFICATION",
    "priority": "MEDIUM",
    "status": "pending",
    "payload": { "lead_name": "Acme Corp" }
  }
]
```

### POST /api/platform/events

Fire a custom event into the event bus.

**Auth:** Required
**Request body:**
```json
{
  "name": "campaign_complete",
  "payload": { "campaign_id": "camp-42" },
  "source": "marketing-bot"
}
```
**Response:**
```json
{
  "event": "campaign_complete",
  "notified": 1
}
```

---

## 5. Approvals

All `/api/approvals` paths bypass auth checks (public for local dev).

### GET /api/approvals

List pending HITL (human-in-the-loop) approval requests.

**Auth:** Not required
**Query parameters:**
| Parameter  | Type   | Description              |
|------------|--------|--------------------------|
| `category` | string | Filter by approval category |

**Request body:** None
**Response:** Array of pending approval objects:
```json
[
  {
    "request_id": "req-001",
    "category": "financial",
    "description": "Wire transfer of $50,000 to Acme Corp",
    "risk_assessment": "high",
    "title": "Approve wire transfer",
    "overdue": false
  }
]
```

### GET /api/approvals/{request_id}

Get detail for a single approval request.

**Auth:** Not required
**Request body:** None
**Response:** Single approval object (same shape as list items, with full detail).

### POST /api/approvals/{request_id}/approve

Approve a pending HITL request. Records an audit entry.

**Auth:** Not required
**Request body:**
```json
{
  "reason": "Verified by finance team",
  "approved_by": "admin@example.com"
}
```
All fields are optional.
**Response:**
```json
{ "success": true }
```

### POST /api/approvals/{request_id}/reject

Reject a pending HITL request. Records an audit entry.

**Auth:** Not required
**Request body:**
```json
{
  "reason": "Amount exceeds policy limit",
  "rejected_by": "admin@example.com"
}
```
All fields are optional.
**Response:**
```json
{ "success": true }
```

---

## 6. Admin Chat

### POST /api/admin/chat

Chat with the admin orchestrator. Recognizes built-in commands (list agents, system
status, show approvals, start/stop agent, approve/reject, help, greetings, workflows)
and handles them instantly. Unrecognized messages fall through to the LLM-backed
admin-orchestrator agent.

**Auth:** Not required
**Request body:**
```json
{
  "message": "system status",
  "session_id": "default"
}
```
**Response:**
```json
{
  "response": "**System Status:**\n- Agents: **41** total, **5** running\n...",
  "session_id": "default",
  "turns": 3
}
```

### POST /api/admin/chat/stream

SSE streaming version of admin chat. Known commands return instantly; open-ended
questions stream tokens from the LLM router (Anthropic or OpenAI). Falls back to
chunked emulation via the legacy admin invoker if no real LLM provider is configured.

**Auth:** Not required
**Request body:**
```json
{
  "message": "Explain our lead scoring model",
  "session_id": "default"
}
```
**Response:** `text/event-stream` (SSE):
```
data: {"type": "thinking", "content": "Processing..."}

data: {"type": "text_delta", "content": "Our lead scoring "}

data: {"type": "text_delta", "content": "model uses..."}

data: {"type": "done", "tokens_used": 340}
```

### GET /api/admin/health

Admin health overview combining agent summary, pending approvals, workflow counts,
and company metrics.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "agents": { "total": 41, "running": 5 },
  "approvals": { "pending": 3 },
  "workflows": { "active": 1 },
  "metrics": {}
}
```

### GET /api/admin/metrics

Aggregated platform metrics: usage (daily/monthly), audit counts (last 24h), agent
breakdown by stack/execution type, scheduler status, approvals, and workflows.

**Auth:** Not required
**Query parameters:**
| Parameter     | Type   | Description                                     |
|---------------|--------|-------------------------------------------------|
| `metric_name` | string | Return only this sub-tree (e.g. `agents`, `usage`) |

**Request body:** None
**Response:**
```json
{
  "usage": {
    "daily": { "tokens": 52000, "cost_usd": 1.30 },
    "monthly": { "tokens": 1200000, "cost_usd": 30.00 }
  },
  "audit": {
    "total_24h": 147,
    "by_action_24h": { "agent.deploy": 5, "approval.approve": 12 }
  },
  "agents": {
    "total": 41,
    "running": 5,
    "by_stack": { "forgeos": 30, "crewai": 11 },
    "by_execution_type": { "always_on": 3, "scheduled": 10 }
  },
  "scheduler": {
    "jobs": 10,
    "max_lag_seconds": 0.0
  },
  "approvals": { "pending": 3 },
  "workflows": { "active": 1 },
  "timestamp": "2026-04-12T14:30:00+00:00"
}
```

### GET /api/admin/events

Query events via admin tools.

**Auth:** Not required
**Query parameters:**
| Parameter    | Type   | Description                 |
|--------------|--------|-----------------------------|
| `department` | string | Filter by department        |
| `status`     | string | Filter by event status      |
| `priority`   | string | Filter by priority          |

**Request body:** None
**Response:** Array of event objects.

### GET /api/admin/knowledge

Search the knowledge base.

**Auth:** Not required
**Query parameters:**
| Parameter  | Type   | Description              |
|------------|--------|--------------------------|
| `query`    | string | Search query string      |
| `category` | string | Filter by category       |

**Request body:** None
**Response:** Array of knowledge entries.

### POST /api/admin/knowledge

Add a new knowledge entry. Returns 201 on success.

**Auth:** Required
**Request body:**
```json
{
  "title": "Q3 Pipeline Strategy",
  "content": "Focus on enterprise accounts with ARR > $100k...",
  "category": "decision",
  "tags": ["sales", "strategy"],
  "source": "exec-meeting-2026-04-10"
}
```
**Response:** The created knowledge entry object.

---

## 7. Intelligence

### POST /api/intelligence/ask

Ask a business intelligence question. Tries the intel-analyst agent first, then falls
back to direct ontology queries.

**Auth:** Not required
**Request body:**
```json
{
  "question": "Which customers are at risk of churning?",
  "session_id": "default"
}
```
**Response:**
```json
{
  "response": "Based on engagement metrics, 3 customers show churn risk...",
  "session_id": "default",
  "turns": 1
}
```

### GET /api/intelligence/ontology/schema

Get the ontology type definitions and relationship (link) types.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "types": [
    { "name": "Customer", "properties": ["name", "stage", "arr"] }
  ],
  "link_types": [
    { "name": "OWNS", "source": "Customer", "target": "Deal" }
  ]
}
```

### GET /api/intelligence/ontology/objects

Query ontology objects by type.

**Auth:** Not required
**Query parameters:**
| Parameter | Type    | Required | Description                     |
|-----------|---------|----------|---------------------------------|
| `type`    | string  | Yes      | Object type to query            |
| `limit`   | integer | No       | Max results (default 50)        |

**Request body:** None
**Response:**
```json
[
  {
    "id": "obj-001",
    "type": "Customer",
    "properties": { "name": "Acme Corp", "stage": "active", "arr": 120000 },
    "source": "crm_sync",
    "created_at": "2026-03-15T10:00:00+00:00"
  }
]
```

### POST /api/intelligence/connectors/sync

Trigger a manual data sync from all configured connectors. Returns 202 Accepted.

**Auth:** Required
**Request body:** None
**Response:**
```json
{
  "status": "accepted",
  "message": "Sync triggered (background)"
}
```

---

## 8. Audit

### GET /api/audit

Query the audit log with optional filters.

**Auth:** Not required
**Query parameters:**
| Parameter       | Type    | Description                              |
|-----------------|---------|------------------------------------------|
| `limit`         | integer | Max entries to return (1-1000, default 100) |
| `resource_type` | string  | Filter by resource type (e.g. `agent`)   |
| `resource_id`   | string  | Filter by resource ID                    |
| `action`        | string  | Filter by action (e.g. `agent.deploy`)   |
| `since`         | string  | ISO 8601 timestamp lower bound           |

**Request body:** None
**Response:** Array of audit log entries:
```json
[
  {
    "timestamp": "2026-04-12T14:30:00+00:00",
    "action": "agent.deploy",
    "actor": "api",
    "resource_type": "agent",
    "resource_id": "sales-lead-gen",
    "details": { "name": "Lead Generator", "stack": "forgeos" }
  }
]
```

---

## 9. Billing and Usage

### GET /api/billing/usage

Return today's and month-to-date usage for the current tenant, along with plan limits.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "tenant_id": "default",
  "plan": "starter",
  "daily": { "tokens": 52000, "cost_usd": 1.30 },
  "monthly": { "tokens": 1200000, "cost_usd": 30.00 },
  "limits": {
    "daily_tokens": 500000,
    "daily_workflows": 50,
    "max_agents": 100,
    "max_mcp_servers": 20
  }
}
```

---

## 10. Providers

### GET /api/admin/providers

Return configuration status for each LLM provider. Does NOT expose secret values.
Also returns feature flags for optional integrations (real HTTP, GitHub, messaging,
CRM).

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "providers": {
    "anthropic": {
      "configured": true,
      "client_initialized": true,
      "env_var": "ANTHROPIC_API_KEY"
    },
    "openai": {
      "configured": true,
      "client_initialized": true,
      "env_var": "OPENAI_API_KEY"
    },
    "google": {
      "configured": false,
      "client_initialized": false,
      "env_var": "GOOGLE_API_KEY",
      "sdk_installed": false
    }
  },
  "feature_flags": {
    "real_http": false,
    "real_github": false,
    "real_messaging": false,
    "real_crm": false
  },
  "available_providers": ["anthropic", "openai"]
}
```

---

## 11. Auth

### POST /api/auth/token

Dev-mode login endpoint. When `FORGEOS_ALLOW_DEV_LOGIN=1` (default in local dev),
accepts a password (default `"forgeos"`, override via `FORGEOS_DEV_PASSWORD`) and
returns a session token. For production, replace with a real JWT/Firebase/OAuth flow.

**Auth:** Not required
**Request body:**
```json
{
  "password": "forgeos"
}
```
**Response:**
```json
{
  "token": "dev-a1b2c3d4e5f6...",
  "user": {
    "user_id": "dev-user",
    "email": "dev@forgeos.local",
    "tenant_id": "default",
    "role": "admin",
    "name": "Dev User"
  }
}
```

### GET /api/me

Return the current user based on the `Authorization` (Bearer) or `X-API-Key` header.
In dev mode, any `dev-*` bearer token returns the static dev user. Returns 401 if no
valid credential is provided.

**Auth:** Not required (but returns 401 without a valid header)
**Request body:** None
**Response:**
```json
{
  "user_id": "dev-user",
  "email": "dev@forgeos.local",
  "tenant_id": "default",
  "role": "admin",
  "name": "Dev User"
}
```

---

## 12. Clients

Client management for per-client agent infrastructure (multi-tenant scoping).

### POST /api/clients

Create a new client for scoped agent deployments. Returns 201 on success, 409 if
the client ID already exists.

**Auth:** Required
**Request body:**
```json
{
  "id": "acme-corp",
  "name": "Acme Corporation",
  "config": {}
}
```
**Response (201):**
```json
{
  "id": "acme-corp",
  "name": "Acme Corporation",
  "config": {},
  "agent_count": 0,
  "mcp_server_count": 0
}
```

### GET /api/clients

List all clients with enriched agent and MCP server counts.

**Auth:** Not required
**Request body:** None
**Response:**
```json
[
  {
    "id": "acme-corp",
    "name": "Acme Corporation",
    "config": {},
    "agent_count": 3,
    "mcp_server_count": 2
  }
]
```

### GET /api/clients/{client_id}

Get client details including MCP server configurations (secrets redacted).

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "id": "acme-corp",
  "name": "Acme Corporation",
  "config": {},
  "agent_count": 3,
  "mcp_server_count": 2,
  "mcp_servers": [
    {
      "server_name": "slack",
      "package": "@anthropic/mcp-slack",
      "env_vars": { "SLACK_TOKEN": "***" },
      "args": []
    }
  ]
}
```

### DELETE /api/clients/{client_id}

Archive a client. Returns 404 if the client does not exist.

**Auth:** Required
**Request body:** None
**Response:**
```json
{ "ok": true, "status": "archived" }
```

### GET /api/clients/{client_id}/agents

List all agents scoped to a specific client.

**Auth:** Not required
**Request body:** None
**Response:** Array of agent definition objects (same shape as `GET /api/platform/agents`).

### POST /api/clients/{client_id}/mcp-servers

Add an MCP server configuration for a client. Returns 201 on success, 409 if the
server name already exists for that client.

**Auth:** Required
**Request body:**
```json
{
  "server_name": "slack",
  "package": "@anthropic/mcp-slack",
  "env_vars": { "SLACK_TOKEN": "xoxb-..." },
  "args": []
}
```
**Response (201):** The created MCP config object.

### GET /api/clients/{client_id}/mcp-servers

List MCP server configs for a client. Secret values in `env_vars` are redacted.

**Auth:** Not required
**Request body:** None
**Response:**
```json
[
  {
    "server_name": "slack",
    "package": "@anthropic/mcp-slack",
    "env_vars": { "SLACK_TOKEN": "***" },
    "args": []
  }
]
```

### PUT /api/clients/{client_id}/mcp-servers/{server_name}

Update an MCP server config for a client.

**Auth:** Required
**Request body:**
```json
{
  "server_name": "slack",
  "package": "@anthropic/mcp-slack",
  "env_vars": { "SLACK_TOKEN": "xoxb-new-token" },
  "args": ["--debug"]
}
```
**Response:** The updated MCP config object. Returns 404 if the server is not found.

### DELETE /api/clients/{client_id}/mcp-servers/{server_name}

Remove an MCP server config from a client.

**Auth:** Required
**Request body:** None
**Response:**
```json
{ "ok": true }
```

---

## 13. Workflows

### GET /api/workflows

List running workflows with progress summaries.

**Auth:** Not required
**Request body:** None
**Response:**
```json
[
  {
    "id": "wf-001",
    "name": "Onboarding Pipeline",
    "type": "sequential",
    "status": "running",
    "priority": "medium",
    "progress": { "total": 5, "completed": 3 }
  }
]
```

### GET /api/workflows/{workflow_id}

Get a workflow's full progress report.

**Auth:** Not required
**Request body:** None
**Response:** Workflow progress report object (structure varies by workflow type).

---

## 14. Inter-Agent Messaging

### GET /api/platform/messages/{agent_id}

Read messages from the event bus mailbox for a specific agent.

**Auth:** Not required
**Query parameters:**
| Parameter | Type    | Description                     |
|-----------|---------|---------------------------------|
| `unread`  | boolean | Only unread messages (default true) |

**Request body:** None
**Response:** Array of message objects.

### POST /api/platform/messages

Send an inter-agent message. Returns 201.

**Auth:** Required
**Request body:**
```json
{
  "from_agent_id": "exec-ceo",
  "to_agent_id": "sales-lead-gen",
  "content": { "instruction": "Prioritize enterprise leads" }
}
```
**Response:**
```json
{ "message_id": "a1b2c3d4" }
```

---

## 15. Scheduler

### GET /api/platform/scheduler

List all scheduled jobs from the platform scheduler.

**Auth:** Not required
**Request body:** None
**Response:** Array of scheduled job objects:
```json
[
  {
    "agent_id": "daily-report-gen",
    "schedule": "0 9 * * *",
    "next_run_at": "2026-04-13T09:00:00+00:00",
    "last_run_at": "2026-04-12T09:00:00+00:00"
  }
]
```

---

## 16. Skills

### GET /api/skills/domains

List all skill domains with counts.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "total": 42,
  "domains": [
    { "name": "sales", "count": 12 },
    { "name": "engineering", "count": 8 }
  ]
}
```

### GET /api/skills/search

Search skills by keyword, optionally filtered by domain.

**Auth:** Not required
**Query parameters:**
| Parameter | Type   | Required | Description          |
|-----------|--------|----------|----------------------|
| `query`   | string | Yes      | Search keyword       |
| `domain`  | string | No       | Filter by domain     |

**Request body:** None
**Response:**
```json
{
  "count": 3,
  "skills": [
    { "name": "cold-outreach", "domain": "sales", "description": "..." }
  ]
}
```

### GET /api/skills/{name}

Get full skill content by name.

**Auth:** Not required
**Request body:** None
**Response:** Full skill object including content. Returns 404 if not found.

---

## 17. MCP Registry

### GET /api/mcps/categories

List all MCP package categories with counts.

**Auth:** Not required
**Request body:** None
**Response:**
```json
{
  "total": 25,
  "categories": [
    { "name": "communication", "count": 6 },
    { "name": "data", "count": 10 }
  ]
}
```

### GET /api/mcps/search

Search MCP packages by keyword, optionally filtered by category.

**Auth:** Not required
**Query parameters:**
| Parameter  | Type   | Required | Description          |
|------------|--------|----------|----------------------|
| `query`    | string | Yes      | Search keyword       |
| `category` | string | No       | Filter by category   |

**Request body:** None
**Response:**
```json
{
  "count": 2,
  "packages": [
    { "name": "@anthropic/mcp-slack", "category": "communication", "description": "..." }
  ]
}
```

### GET /api/mcps/{name}

Get full MCP package details including connection config. The `name` parameter
supports slashes (path-style, e.g. `@anthropic/mcp-slack`).

**Auth:** Not required
**Request body:** None
**Response:** Full MCP package object. Returns 404 if not found.

---

## 18. WebSocket

### WS /ws/agents

Real-time agent status stream. After connection, the server pushes a JSON status
update every 5 seconds with all registered agents and their current state.

**Auth:** Not required
**Message format (server to client):**
```json
{
  "timestamp": "2026-04-12T14:30:05+00:00",
  "agents": [
    { "agent_id": "exec-ceo", "name": "CEO Agent", "status": "running" }
  ],
  "total": 41,
  "running": 5
}
```

---

## Pydantic Request Models Reference

| Model                 | Used by                                              |
|-----------------------|------------------------------------------------------|
| `ChatRequest`         | POST /api/admin/chat, POST /api/admin/chat/stream    |
| `ChatResponse`        | Response for admin chat and intelligence ask          |
| `InvokeRequest`       | POST /api/platform/agents/{id}/invoke                |
| `AgentCreateRequest`  | POST /api/platform/agents, PUT /api/platform/agents/{id} |
| `AgentChatRequest`    | POST /api/platform/agents/{id}/chat/stream           |
| `ApprovalAction`      | POST /api/approvals/{id}/approve, .../reject         |
| `EventFireRequest`    | POST /api/platform/events                            |
| `IntelligenceRequest` | POST /api/intelligence/ask                           |
| `KnowledgeAddRequest` | POST /api/admin/knowledge                            |
| `MessageSendRequest`  | POST /api/platform/messages                          |
| `ClientCreateRequest` | POST /api/clients                                    |
| `ClientMCPConfigRequest` | POST/PUT /api/clients/{id}/mcp-servers            |
| `DevTokenRequest`     | POST /api/auth/token                                 |

---

## Rate Limiting

The API applies in-memory rate limiting per client:

| Operation type | Limit            |
|----------------|------------------|
| Read (GET)     | 120 requests / 60s |
| Write (POST/PUT/DELETE) | 20 requests / 60s |

Exceeding the limit returns HTTP 429.

---

## 19. AgentOS Kernel

The kernel is the policy decision point for every agent action. These endpoints let SDK clients (in-process or remote) and external agents check permissions, inspect contracts, and record audit events.

### POST /api/platform/kernel/check-tool

Check if an agent is allowed to call a tool. Runs the composite flow: permissions -> budget -> policies.

**Auth:** Not required (intended for SDK clients)
**Request body:**
```json
{
  "agent_id": "81ea3939-01c",
  "tool_name": "mcp__filesystem__read_file",
  "tool_input": { "path": "/tmp/data.txt" },
  "estimated_cost_usd": 0.02
}
```
**Response:**
```json
{
  "action": "allow",
  "reason": "tool call permitted",
  "details": { "tool": "mcp__filesystem__read_file", "namespace": "default" },
  "audit_id": "abc123",
  "timestamp": "2026-04-16T10:00:00+00:00"
}
```

`action` is one of `allow`, `deny`, `mask`, `ask_human`, `rate_limit`.

### POST /api/platform/kernel/check-a2a

Check if caller may invoke target agent.

**Auth:** Not required
**Request body:**
```json
{
  "caller_agent_id": "81ea3939-01c",
  "target_namespace": "sales",
  "target_name": "cfo"
}
```
**Response:** `KernelDecision` (same shape as check-tool).

### POST /api/platform/kernel/check-data

Check if agent may access data in the target namespace.

**Auth:** Not required
**Request body:**
```json
{
  "agent_id": "81ea3939-01c",
  "target_namespace": "finance-pii"
}
```
**Response:** `KernelDecision`.

### GET /api/platform/kernel/contract/{agent_id}

Retrieve the agent's full contract for self-introspection.

**Auth:** Not required
**Response:** Full `AgentDefinition` as JSON, with `metadata._*` containing the v2 AgentOS fields (namespace, labels, boundaries, governance, etc.).

### POST /api/platform/kernel/admit

Validate a contract before deploy. Returns structured errors + warnings.

**Auth:** Auth Required
**Request body:** A full agent manifest dict (same shape as `POST /api/platform/agents` body).
**Response:**
```json
{
  "admitted": true,
  "reason": "admitted",
  "errors": [],
  "warnings": ["Tools not currently available: ['mcp__foo__bar']"],
  "agent_uid": "9ae3b7f1-8c0",
  "generation": 1
}
```

### POST /api/platform/kernel/audit

Record a custom audit event from an agent.

**Auth:** Not required
**Request body:**
```json
{
  "agent_id": "81ea3939-01c",
  "event": "decision_made",
  "details": { "choice": "approved", "confidence": 0.92 }
}
```
**Response:** `{ "ok": true }`

---

## Interactive Documentation

When the server is running, auto-generated interactive docs are available at:

- **Swagger UI:** `/docs`
- **ReDoc:** `/redoc`
- **OpenAPI JSON:** `/openapi.json`
