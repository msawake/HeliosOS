# A2H Protocol — Agent-to-Human Interaction

A companion protocol to [A2A](https://github.com/google/A2A) (agent-to-agent) and [MCP](https://modelcontextprotocol.io) (agent-to-tool).

```
MCP:  Agent ↔ Tool     (how agents use tools)
A2A:  Agent ↔ Agent    (how agents collaborate)
A2H:  Agent → Human    (how agents ask humans)
H2A:  Human → Agent    (A2A from a UI — not a separate protocol)
```

## Why A2H

A2A defined how agents talk to each other. MCP defined how agents use tools. But we are now building systems where **humans and agents collaborate in the same workflows** — sales pipelines, support operations, compliance reviews, medical decisions — and there is no standard for the moment an agent needs human input.

A2H fills this gap. It defines a structured, transport-agnostic protocol for agents to ask humans questions and receive responses.

**What about H2A (human calls agent)?** H2A is not a new protocol — it's A2A initiated from a UI. When a human delegates work to an agent, the underlying protocol is A2A; the human just needs an interface (dashboard, Slack, API) that speaks A2A on their behalf. A2H is the genuinely novel part: agents reaching out to humans with structured questions, deadlines, escalation chains, and auto-delegation rules.

## What Makes It Different From A2A

Humans are not fast agents. Seven things change when the target is human:

1. **Async with deadlines** — responses take minutes/hours, not milliseconds
2. **Structured questions** — choices, approvals, forms — not free text
3. **Delivery channels** — dashboard, Slack, email, SMS — not function calls
4. **Availability states** — busy, offline, on break — not always-on
5. **Auto-delegation** — routine requests auto-answered by rules
6. **Escalation chains** — timeout-based promotion to next human
7. **SLA enforcement** — deadlines with automatic expiry

## Protocol Contents

```
docs/protocols/
  a2h-spec.md                        # Full specification
  README.md                          # This file
  schemas/
    request.json                     # Request JSON Schema
    response.json                    # Response JSON Schema  
    notification.json                # Notification JSON Schema
    delegation-rule.json             # Delegation Rule JSON Schema
    participant-card.json            # Participant Card JSON Schema (extends A2A Agent Cards)
```

## Quick Example

Agent asks a human to approve a deal:

```json
POST /a2h/v1/requests

{
  "from": {"name": "sales-agent", "namespace": "sales"},
  "to": {"name": "sarah", "namespace": "sales"},
  "content": {
    "question": "Approve the MegaInc deal at $2.5M?",
    "response_type": "choice",
    "options": [
      {"label": "Approve", "value": "approve"},
      {"label": "Reject", "value": "reject"}
    ],
    "context": {"deal_value": 2500000, "bant_score": 87}
  },
  "priority": "high",
  "deadline": "2026-04-25T16:00:00Z"
}
```

Human responds via Slack:

```json
POST /a2h/v1/requests/req_abc/respond

{
  "response": {"value": "approve", "text": "Good fit. Proceed."},
  "channel": "slack"
}
```

## Reference Implementation

The ForgeOS platform includes a reference implementation:

- `src/platform/h2a.py` — H2AGateway, HumanAgent, HumanRequest, delivery channels
- `tests/test_h2a_protocol.py` — Protocol conformance tests
- `tests/test_platform_generic.py` — State machine, escalation, delegation tests

## License

Apache 2.0
