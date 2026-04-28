# A2H Protocol Specification v0.1

**Agent-to-Human Interaction Protocol**

A companion to [A2A](https://github.com/google/A2A) (agent-to-agent) and [MCP](https://modelcontextprotocol.io) (agent-to-tool). A2H defines how AI agents ask humans structured questions and receive structured responses in collaborative workflows.

```
MCP:  Agent ↔ Tool     (how agents use tools)
A2A:  Agent ↔ Agent    (how agents collaborate)
A2H:  Agent ↔ Human    (how agents and humans collaborate)
```

---

## Introduction

Google's A2A protocol solved a critical problem: how do AI agents talk to each other? With A2A, an agent can discover, call, and delegate work to other agents across frameworks, platforms, and organizations. Anthropic's MCP solved another: how do agents use tools? With MCP, any agent can connect to any tool server through a standard interface.

But we are now in a world where the real work isn't done by agents alone or humans alone — it's done by **humans and agents collaborating together** in workflows that span both. A sales pipeline where an agent researches leads and a human approves deals. A support operation where agents monitor sentiment and humans handle escalations. A compliance workflow where agents flag violations and humans decide the response.

In these workflows, agents need to ask humans questions — not just "approve yes/no" but structured decisions with context, deadlines, and escalation paths. And humans need to delegate work to agents — not through custom UIs but through a standard protocol that any system can implement.

### Where does H2A fit?

A natural question: should this spec cover both directions — Agent-to-Human (A2H) AND Human-to-Agent (H2A)?

**H2A (human calls agent) is not a new protocol.** When a human delegates work to an agent, they are doing exactly what A2A defines: addressing a target by namespace/name, sending a task, and receiving a result. The only difference is that the caller is human, initiating from a UI rather than from code. H2A is an **implementation concern** — you give humans an interface (dashboard, Slack command, API) that speaks A2A on their behalf. The protocol is A2A. The UI is H2A.

**A2H (agent calls human) IS a new protocol.** When an agent needs something from a human, everything changes. Humans respond in minutes or hours, not milliseconds. Humans need structured choices, not JSON payloads. Humans have availability states — they might be in a meeting, on break, or off shift. Humans can delegate routine decisions to auto-response rules. None of this exists in A2A because A2A assumes the target responds immediately and programmatically.

This specification therefore focuses on **A2H** — the genuinely novel protocol for agents asking humans structured questions. H2A is mentioned where relevant but does not require its own protocol; it is A2A with a human-facing UI layer.

The complete picture:

```
MCP:  Agent ↔ Tool     (published — how agents use tools)
A2A:  Agent ↔ Agent    (published — how agents collaborate)
H2A:  Human → Agent    (A2A from a UI — implementation, not protocol)
A2H:  Agent → Human    (this specification — how agents ask humans)
```

---

## Abstract

In every real-world AI agent deployment, agents reach a point where they need human input: an approval, a decision, clarification, or review. Today this is handled through ad-hoc integrations — Slack bots, email triggers, custom UIs — with no standard protocol.

A2H defines a structured, transport-agnostic protocol for agents to ask humans questions and receive responses. It handles the fundamental differences between agent and human participants: humans respond asynchronously (minutes/hours), need structured UIs (not raw JSON), have availability states, and may delegate routine decisions automatically.

A2H does not replace A2A or MCP. It complements them. A workflow that uses all three looks like:

```
Human delegates task to agent        (H2A — A2A from a UI)
  → Agent researches                 (MCP tools)
  → Agent calls specialist agent     (A2A)
  → Agent asks human for approval    (A2H — this protocol)
  → Human approves via Slack
  → Agent executes the decision      (MCP tools)
  → Agent notifies human             (A2H notification)
```

## Design Principles

1. **Structured, not free-text.** Agents send typed questions (choice, approval, text, number, confirm, form). Humans see UI elements, not prompts.

2. **Async-first.** Every request has a deadline. The agent doesn't block — it gets a request ID and can poll or subscribe to updates.

3. **Transport-agnostic.** The protocol defines the message format. How it reaches the human (dashboard, Slack, email, SMS) is a channel adapter concern, not protocol.

4. **Complements A2A.** Uses the same addressing model (namespace/name). An A2A-compatible system can add A2H support without changing its agent infrastructure.

5. **Stateful lifecycle.** Requests transition through defined states. Agents can query status at any time.

6. **Delegation-aware.** Humans can define rules that auto-respond to routine requests, reducing unnecessary interruptions.

---

## Key Concepts

### Participant

Any entity in the system — human or agent. Identified by `namespace/name`.

```json
{
  "name": "sarah",
  "namespace": "sales",
  "participant_type": "human"
}
```

### Request

A structured question from an agent to a human. Has a response type, optional choices, deadline, priority, and context.

### Response

A structured answer from a human to an agent. Shape depends on the request's `response_type`.

### Notification

A one-way message from an agent to a human. No response expected.

### Channel

How a request reaches a human. Examples: dashboard notification, Slack DM, email, SMS, mobile push. Channels are implementation-specific — the protocol does not define them.

### Delegation Rule

A human-configured rule that auto-responds to matching requests. Example: "auto-approve all requests from sales agents for amounts under $10,000."

### Escalation Chain

An ordered list of humans to try. If level N doesn't respond within the timeout, promote to level N+1.

---

## Data Model

### Request Object

```json
{
  "protocol": "a2h/v1",
  "id": "req_7f3a2b",
  "type": "request",

  "from": {
    "name": "research-analyst",
    "namespace": "sales",
    "participant_type": "agent"
  },
  "to": {
    "name": "sarah",
    "namespace": "sales",
    "participant_type": "human"
  },

  "content": {
    "question": "Should we proceed with the MegaInc deal at $2.5M?",
    "response_type": "choice",
    "options": [
      {"label": "Approve", "value": "approve", "description": "Proceed to contract negotiation"},
      {"label": "Counter", "value": "counter", "description": "Propose different terms"},
      {"label": "Reject", "value": "reject", "description": "Walk away from this deal"}
    ],
    "context": {
      "deal_value": 2500000,
      "bant_score": 87,
      "risk_level": "medium",
      "competitor_engagement": "none",
      "recommendation": "approve",
      "supporting_data": "MegaInc: 2400 employees, Series D, $180M ARR, CTO is decision maker"
    }
  },

  "priority": "high",
  "deadline": "2026-04-25T16:00:00Z",
  "escalation": {
    "chain": [
      {"target": "sales/sarah", "timeout_minutes": 60},
      {"target": "sales/tom", "timeout_minutes": 120}
    ],
    "current_level": 0
  },

  "status": "pending",
  "created_at": "2026-04-25T12:00:00Z",
  "updated_at": "2026-04-25T12:00:00Z"
}
```

### Response Types

| Type | Description | Human UI | Response Shape |
|------|-------------|----------|---------------|
| `choice` | Select one option from a list | Buttons or radio group | `{"value": "approve"}` |
| `approval` | Binary approve/reject | Toggle or two buttons | `{"approved": true, "reason": "..."}` |
| `text` | Free-text answer | Text area | `{"text": "..."}` |
| `number` | Numeric input | Number field | `{"value": 42.5}` |
| `confirm` | Yes/no confirmation | Checkbox or toggle | `{"confirmed": true}` |
| `form` | Multi-field structured input | Form with typed fields | `{"fields": {"name": "...", "amount": 100}}` |

### Response Object

```json
{
  "protocol": "a2h/v1",
  "request_id": "req_7f3a2b",
  "from": {
    "name": "sarah",
    "namespace": "sales",
    "participant_type": "human"
  },
  "response": {
    "value": "approve",
    "text": "Good fit. Proceed to contract. Require 3-year minimum commitment.",
    "metadata": {
      "conditions": ["3-year minimum", "Net-60 payment terms"]
    }
  },
  "responded_at": "2026-04-25T12:15:00Z",
  "channel": "slack"
}
```

### Notification Object

```json
{
  "protocol": "a2h/v1",
  "id": "notif_8c4d1e",
  "type": "notification",
  "from": {
    "name": "dashboard-reporter",
    "namespace": "operations",
    "participant_type": "agent"
  },
  "to": {
    "name": "tom",
    "namespace": "operations",
    "participant_type": "human"
  },
  "content": {
    "message": "CSAT dropped 0.8 points in the last 2 hours. Correlated with billing system outage.",
    "severity": "warning",
    "context": {
      "current_csat": 3.4,
      "previous_csat": 4.2,
      "root_cause": "billing_system_outage",
      "affected_calls_pct": 45
    }
  },
  "priority": "high",
  "created_at": "2026-04-25T14:30:00Z"
}
```

### Priority Levels

| Priority | Meaning | Expected Delivery |
|----------|---------|-------------------|
| `critical` | Requires immediate human attention | All channels simultaneously |
| `high` | Important, should be seen within minutes | Primary channel + fallback |
| `medium` | Normal workflow, hours are acceptable | Primary channel |
| `low` | Informational, no urgency | Dashboard only (batch OK) |

### Lifecycle States

```
                    ┌──────────┐
                    │ created  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
              ┌─────│ pending  │─────┐──────────┐
              │     └────┬─────┘     │          │
              │          │           │          │
         ┌────▼─────┐ ┌─▼────────┐ ┌▼────────┐ ┌▼──────────┐
         │ answered │ │ expired  │ │cancelled│ │auto_delegated│
         └──────────┘ └──────────┘ └─────────┘ └────────────┘
                                        │
                                   ┌────▼─────┐
                                   │escalated │
                                   │(→ pending │
                                   │ at next  │
                                   │ level)   │
                                   └──────────┘
```

| State | Meaning |
|-------|---------|
| `created` | Request constructed, not yet delivered |
| `pending` | Delivered to human, awaiting response |
| `answered` | Human responded |
| `expired` | Deadline passed without response |
| `cancelled` | Agent withdrew the request |
| `escalated` | Promoted to next level in escalation chain |
| `auto_delegated` | Matched a delegation rule, auto-responded |

---

## HTTP Transport

All endpoints use JSON. Authentication is required but the method is implementation-specific (OAuth 2.0, API keys, JWT).

### Endpoints

#### Create Request

```
POST /a2h/v1/requests

Body: Request Object (without id, status, created_at — server generates these)

Response: 201 Created
{
  "id": "req_7f3a2b",
  "status": "pending",
  "deadline": "2026-04-25T16:00:00Z"
}
```

#### Get Request Status

```
GET /a2h/v1/requests/{id}

Response: 200 OK
{
  // Full Request Object with current status and response (if answered)
}
```

#### Submit Response

```
POST /a2h/v1/requests/{id}/respond

Body:
{
  "response": {
    "value": "approve",
    "text": "Looks good. Proceed."
  },
  "channel": "slack"
}

Response: 200 OK
{
  "request_id": "req_7f3a2b",
  "status": "answered"
}
```

#### Cancel Request

```
POST /a2h/v1/requests/{id}/cancel

Body:
{
  "reason": "No longer needed — deal fell through"
}

Response: 200 OK
{
  "request_id": "req_7f3a2b",
  "status": "cancelled"
}
```

#### List Pending Requests

```
GET /a2h/v1/requests?to={namespace}/{name}&status=pending

Response: 200 OK
{
  "requests": [
    { /* Request Object */ },
    { /* Request Object */ }
  ]
}
```

#### Send Notification

```
POST /a2h/v1/notifications

Body: Notification Object (without id — server generates)

Response: 201 Created
{
  "id": "notif_8c4d1e",
  "delivered": true
}
```

#### Subscribe to Updates (SSE)

```
GET /a2h/v1/requests/{id}/events
Accept: text/event-stream

Events:
  event: status_changed
  data: {"status": "answered", "responded_at": "..."}

  event: escalated
  data: {"from_level": 0, "to_level": 1, "new_target": "sales/tom"}
```

---

## Delegation Rules

Humans can configure rules that auto-respond to matching requests without human involvement.

### Rule Schema

```json
{
  "rules": [
    {
      "name": "auto_approve_small_deals",
      "match": {
        "from_namespace": "sales",
        "from_name_pattern": "sales-*",
        "response_type": "approval",
        "context_conditions": {
          "deal_value": {"lt": 10000}
        }
      },
      "auto_response": {
        "approved": true,
        "reason": "Auto-approved: deal value under $10K threshold"
      }
    }
  ]
}
```

### Matching

A delegation rule matches when ALL conditions are satisfied:

| Condition | Meaning |
|-----------|---------|
| `from_namespace` | Request is from this namespace |
| `from_name_pattern` | Sender name matches glob pattern (`sales-*`) |
| `response_type` | Request asks for this response type |
| `priority` | Request has this priority or lower |
| `context_conditions` | Conditions on the context object (lt, gt, eq, in) |

When a rule matches, the request transitions directly to `auto_delegated` and the agent receives the auto-response immediately. The human is notified that the request was auto-handled.

---

## Escalation Chains

When a request isn't answered within the timeout at the current level, it promotes to the next level.

### Chain Schema

```json
{
  "chain": [
    {
      "target": "sales/rachel",
      "timeout_minutes": 10,
      "priority_override": null
    },
    {
      "target": "sales/tom",
      "timeout_minutes": 30,
      "priority_override": "critical"
    },
    {
      "target": "oncall/director",
      "timeout_minutes": 60,
      "priority_override": "critical"
    }
  ]
}
```

### Behavior

1. Request delivered to level 0 target (`rachel`)
2. If no response within 10 minutes → escalate to level 1 (`tom`), set status to `escalated`, re-deliver with optional priority upgrade
3. If no response within 30 minutes → escalate to level 2 (`director`)
4. If no more levels → status becomes `expired`

The agent sees the escalation via the status endpoint or SSE events.

---

## Human Availability

The protocol defines an optional availability model. Implementations that support it route requests based on the human's current state.

### States

States are implementation-defined. Common patterns:

```json
{
  "current_state": "available",
  "states": {
    "available": {"accepts_requests": true},
    "busy": {"accepts_requests": false, "queue": true},
    "away": {"accepts_requests": false, "reroute_to": "delegate"},
    "offline": {"accepts_requests": false, "reroute_to": "on_call"}
  }
}
```

| Behavior | Effect |
|----------|--------|
| `accepts_requests: true` | Deliver immediately |
| `queue: true` | Hold until state changes to accepting |
| `reroute_to: "delegate"` | Deliver to the human's configured delegate |
| `reroute_to: "on_call"` | Deliver to the on-call participant |

The protocol requires that requests are never silently dropped. If a human is unavailable and no reroute target exists, the request stays `pending` until the deadline, then transitions to `expired`.

---

## Relationship to A2A

A2H is designed to coexist with Google's A2A protocol.

### Addressing

A2H uses the same `namespace/name` addressing as A2A. A participant registered in an A2A system can receive A2H requests at the same address.

### Discovery

A2H extends A2A Agent Cards with human-specific fields:

```json
{
  "name": "sarah",
  "namespace": "sales",
  "participant_type": "human",
  "a2h": {
    "supported": true,
    "response_types": ["choice", "approval", "text"],
    "channels": ["dashboard", "slack"],
    "availability": "business_hours"
  }
}
```

Systems that don't support A2H ignore the `a2h` field. Systems that do can route requests to human participants.

### When to use which

| Scenario | Protocol |
|----------|----------|
| Agent delegates research to another agent | A2A |
| Agent asks human to approve a decision | A2H |
| Agent notifies human of a completed task | A2H (notification) |
| Human assigns work to an agent | A2A (from UI) |
| Agent calls a tool/API | MCP |

---

## Relationship to MCP

MCP defines how agents use tools. A2H defines how agents interact with humans. They are orthogonal:

- An agent might use MCP to search a database, then use A2H to ask a human to review the results
- An A2H response might trigger the agent to call MCP tools to execute the human's decision

A2H does not define tools. It defines interactions.

---

## Examples

### Example 1: Simple Approval

An expense agent asks a manager to approve a $500 purchase.

```
Agent → POST /a2h/v1/requests
{
  "from": {"name": "expense-bot", "namespace": "finance"},
  "to": {"name": "david", "namespace": "finance"},
  "content": {
    "question": "Approve purchase order #4521 for $500?",
    "response_type": "approval",
    "context": {"vendor": "AWS", "amount": 500, "category": "infrastructure"}
  },
  "priority": "medium",
  "deadline": "2026-04-26T17:00:00Z"
}

← 201 Created {"id": "req_001", "status": "pending"}

# David approves via Slack 20 minutes later:
POST /a2h/v1/requests/req_001/respond
{"response": {"approved": true, "reason": "Standard infra spend"}, "channel": "slack"}

# Agent checks:
GET /a2h/v1/requests/req_001
← {"status": "answered", "response": {"approved": true, ...}}
```

### Example 2: Multi-Choice with Escalation

A compliance agent detects a policy violation and asks the QA analyst to decide.

```
Agent → POST /a2h/v1/requests
{
  "from": {"name": "compliance-checker", "namespace": "quality"},
  "to": {"name": "michael", "namespace": "quality"},
  "content": {
    "question": "Agent offered 30% discount (policy max: 15%). What action?",
    "response_type": "choice",
    "options": [
      {"label": "Void Discount", "value": "void"},
      {"label": "Approve Exception", "value": "exception"},
      {"label": "Escalate to Manager", "value": "escalate"}
    ],
    "context": {"agent_name": "James", "discount": 30, "max_allowed": 15, "customer": "StartupXYZ"}
  },
  "priority": "high",
  "deadline": "2026-04-25T14:00:00Z",
  "escalation": {
    "chain": [
      {"target": "quality/michael", "timeout_minutes": 15},
      {"target": "operations/tom", "timeout_minutes": 30}
    ]
  }
}
```

If Michael doesn't respond in 15 minutes, the request auto-escalates to Tom with a priority upgrade.

### Example 3: Auto-Delegation

A workforce agent asks Priya (WFM) about overtime. Priya has a delegation rule.

```
Agent → POST /a2h/v1/requests
{
  "from": {"name": "workforce-optimizer", "namespace": "operations"},
  "to": {"name": "priya", "namespace": "operations"},
  "content": {
    "question": "Approve 2 CSRs overtime tomorrow 2-4pm?",
    "response_type": "approval",
    "context": {"estimated_cost": 240, "reason": "forecast spike"}
  },
  "priority": "medium"
}

# Priya's delegation rule: auto-approve overtime < $500
# from operations agents

← 201 Created
{
  "id": "req_003",
  "status": "auto_delegated",
  "response": {
    "approved": true,
    "reason": "Auto-approved: cost $240 under $500 threshold"
  }
}
```

The agent gets an immediate response. Priya sees a notification that the request was auto-handled.

### Example 4: Notification (No Response)

A dashboard agent sends an end-of-day summary.

```
Agent → POST /a2h/v1/notifications
{
  "from": {"name": "dashboard-reporter", "namespace": "operations"},
  "to": {"name": "tom", "namespace": "operations"},
  "content": {
    "message": "Daily summary: 240 calls, FCR 78%, CSAT 4.2, 3 escalations resolved",
    "severity": "info",
    "context": {"calls": 240, "fcr": 0.78, "csat": 4.2}
  },
  "priority": "low"
}

← 201 Created {"id": "notif_004", "delivered": true}
```

---

## Conformance

An A2H-conformant implementation MUST:

1. Accept and validate Request Objects per the schema
2. Return Response Objects with the correct shape for the response_type
3. Implement the lifecycle state machine (pending → answered | expired | cancelled | auto_delegated)
4. Support at least one delivery channel
5. Enforce deadlines (transition to `expired` when deadline passes)
6. Never silently drop requests

An A2H-conformant implementation MAY:

1. Support delegation rules
2. Support escalation chains
3. Support human availability states
4. Support SSE streaming
5. Support multiple delivery channels
6. Support the `form` response type

---

## Security Considerations

1. **Authentication.** All endpoints MUST require authentication. The method is implementation-specific (OAuth 2.0, API keys, mTLS).

2. **Authorization.** Implementations SHOULD verify that the requesting agent is permitted to contact the target human. This may use ACLs, capability tokens, or namespace rules.

3. **Context sanitization.** The `context` object in requests may contain sensitive data (PII, financial data). Implementations MUST apply data boundary policies before delivering to channels (e.g., don't send PII over email).

4. **Channel security.** Delivery channels MUST use encryption in transit (TLS). Channels that deliver to external services (Slack, email) SHOULD evaluate whether the content is appropriate for that channel's security model.

5. **Audit.** Implementations SHOULD record all requests, responses, escalations, and auto-delegations in an audit trail.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-25 | Initial draft |
