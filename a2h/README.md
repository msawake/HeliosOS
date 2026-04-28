# a2h — Agent-to-Human Interaction Protocol

Python reference implementation of the [A2H Protocol](../docs/protocols/a2h-spec.md).

Companion to [A2A](https://github.com/google/A2A) (agent-to-agent) and [MCP](https://modelcontextprotocol.io) (agent-to-tool).

```
pip install a2h
```

## Quick Start

```python
from a2h import Gateway, Participant

# 1. Create gateway
gw = Gateway()

# 2. Register a human
gw.register(Participant(
    name="sarah", namespace="sales",
    role="VP Sales", channels=["dashboard", "slack"],
))

# 3. Agent asks human
req = await gw.ask("sales/sarah",
    question="Approve the MegaInc deal at $2.5M?",
    response_type="choice",
    options=[
        {"label": "Approve", "value": "approve"},
        {"label": "Reject", "value": "reject"},
    ],
    context={"deal_value": 2500000, "bant_score": 87},
    priority="high",
    deadline="4h",
    from_name="sales-agent",
)
print(req.id)      # "req_7f3a2b..."
print(req.status)  # Status.PENDING

# 4. Human responds (from dashboard/Slack/API)
gw.respond(req.id, {"value": "approve", "text": "Good fit."}, channel="slack")

# 5. Agent checks result
result = gw.get(req.id)
print(result.status)          # Status.ANSWERED
print(result.response.value)  # "approve"
```

## Features

### Response Types

```python
# Choice (buttons)
await gw.ask("sales/sarah", question="Pick one", response_type="choice",
    options=[{"label": "A", "value": "a"}, {"label": "B", "value": "b"}])

# Approval (approve/reject)
await gw.ask("sales/sarah", question="Approve $500?", response_type="approval")

# Free text
await gw.ask("sales/sarah", question="What should we do?", response_type="text")

# Number
await gw.ask("sales/sarah", question="How many units?", response_type="number")

# Confirmation (yes/no)
await gw.ask("sales/sarah", question="Proceed?", response_type="confirm")
```

### Auto-Delegation Rules

```python
from a2h import DelegationRule

gw.register(Participant(
    name="priya", namespace="ops",
    delegation_rules=[
        DelegationRule(
            name="auto_approve_small",
            from_namespace="ops",
            response_type="approval",
            context_conditions={"amount": {"lt": 500}},
            auto_response={"approved": True, "reason": "Auto: under $500"},
        ),
    ],
))

# Requests matching the rule are auto-answered immediately
req = await gw.ask("ops/priya", question="Approve $200?",
    response_type="approval", context={"amount": 200}, from_namespace="ops")
print(req.status)  # Status.AUTO_DELEGATED
```

### State-Aware Routing

```python
alice = Participant(name="alice", namespace="eng", delegate="bob")
bob = Participant(name="bob", namespace="eng")
gw.register(alice)
gw.register(bob)

alice.set_state("away")  # reroutes to delegate

req = await gw.ask("eng/alice", question="You there?")
print(req.to_name)  # "bob" (auto-rerouted)
```

### Notifications (No Response)

```python
await gw.notify("sales/sarah",
    message="Daily report: 240 calls, CSAT 4.2",
    severity="info", priority="low")
```

### Async Wait

```python
req = await gw.ask("sales/sarah", question="Approve?", response_type="approval")

# Block until human responds (or timeout)
result = await gw.wait(req.id, timeout=300)
if result.status == Status.ANSWERED:
    print(result.response.approved)
```

### HTTP Server

```python
from a2h import Gateway
from a2h.server import create_app

gw = Gateway()
app = create_app(gw)

# Run: uvicorn app:app --port 8000
# Endpoints:
#   POST /a2h/v1/requests
#   GET  /a2h/v1/requests/{id}
#   POST /a2h/v1/requests/{id}/respond
#   POST /a2h/v1/requests/{id}/cancel
#   GET  /a2h/v1/requests
#   POST /a2h/v1/notifications
#   GET  /.well-known/participants.json
```

### Discovery

```python
cards = gw.discover()
# [{"name": "sarah", "namespace": "sales", "participant_type": "human",
#   "a2h": {"channels": ["dashboard", "slack"], ...}, "protocol": "a2h/v1"}]
```

## Custom Channels

```python
from a2h import Channel, Interaction, Notification

class SlackChannel:
    @property
    def name(self): return "slack"

    async def deliver_request(self, interaction: Interaction) -> bool:
        # Send Slack DM with interactive buttons
        await slack.send_dm(interaction.to_name, ...)
        return True

    async def deliver_notification(self, notification: Notification) -> bool:
        await slack.send_dm(notification.to_name, notification.message)
        return True

gw = Gateway(channels=[SlackChannel()])
```

## Custom Storage

```python
from a2h import Store, Interaction, Response

class PostgresStore:
    def save(self, interaction: Interaction) -> None: ...
    def get(self, interaction_id: str) -> Interaction | None: ...
    def list_pending(self, to_pid: str | None) -> list[Interaction]: ...
    def respond(self, interaction_id: str, response: Response) -> bool: ...
    def cancel(self, interaction_id: str, reason: str) -> bool: ...

gw = Gateway(store=PostgresStore(conn_string="..."))
```

## Package Structure

```
a2h/
  __init__.py      # Public API
  models.py        # Protocol types (Participant, Interaction, Response, ...)
  gateway.py       # A2H Gateway (ask, respond, notify, discover)
  store.py         # Storage protocol + InMemoryStore
  channels.py      # Channel protocol + LogChannel + DashboardChannel
  server.py        # FastAPI HTTP transport
```

## License

Apache 2.0
