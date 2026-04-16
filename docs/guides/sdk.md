# Python SDK

The `forgeos_sdk` package is the primary way developers interact with ForgeOS. It provides:

- **`ForgeOSClient`** — sync HTTP client for agent CRUD + invoke + chat
- **`Agent` / `AgentBuilder`** — declarative + fluent Python APIs for agent definition
- **`AgentManifest`** — Pydantic schema for `agent.yaml` validation
- **`Kernel`** — unified accessor (in-process or HTTP) for policy checks
- **`forgeos` CLI** — `forgeos deploy ./agent.yaml` and friends

## Installation

The SDK ships with ForgeOS:

```bash
cd /Users/jama/Documents/one
pip install -e ".[dev]"
```

## Defining an Agent — Three Ways

### 1. YAML Manifest (recommended — git-committable, reviewable)

```yaml
# agent.yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: email-checker
  namespace: operations
  version: "1.0.0"
  labels:
    domain: ops
    tier: prod

spec:
  runtime:
    framework: forgeos
  lifecycle:
    type: scheduled
    schedule: "0 7,12,17 * * *"
    restart_policy: OnFailure
  llm:
    chat_model: gpt-4o
    provider: openai
  capabilities:
    tools:
      allowed:
        - mcp__filesystem__*
        - company__publish_event
      denied:
        - shell.exec
    a2a:
      canBeCalledBy:
        - namespace: operations
          agents: [ops-manager]
  boundaries:
    budgets:
      daily_usd: 5.00
      per_task_usd: 0.50
    data:
      allowed_namespaces: [ops, public]
      pii_policy: redact
  governance:
    human_in_loop:
      - event: email.send
        approvers: [team-lead]
  system_prompt:
    file: ./prompts/email-checker.md
    variables:
      user_name: jama
    template_engine: jinja2
```

Deploy it:

```bash
forgeos deploy ./agent.yaml
```

### 2. Fluent Builder (programmatic, chainable)

```python
from forgeos_sdk import Agent, ForgeOSClient

manifest = (Agent.builder("email-checker")
    .forgeos()
    .scheduled("0 7,12,17 * * *")
    .model("gpt-4o", provider="openai")
    .tools("mcp__filesystem__*", "company__publish_event")
    .prompt_from_file("./prompts/email-checker.md", variables={"user_name": "jama"})
    .department("operations")
    .guardrails(max_tokens_per_run=10000, max_cost_usd_per_day=5.00)
    .build())

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
```

### 3. Declarative Class (CrewAI-style)

```python
from forgeos_sdk import Agent, ForgeOSClient

class EmailChecker(Agent):
    name = "email-checker"
    description = "Checks email inbox on a schedule"
    department = "operations"
    stack = "forgeos"
    execution_type = "scheduled"
    schedule = "0 7,12,17 * * *"
    model = "gpt-4o"
    provider = "openai"
    tools = ["mcp__filesystem__*", "company__publish_event"]
    system_prompt = "You check email and summarize..."
    max_cost_usd_per_day = 5.00

manifest = EmailChecker.manifest()

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
```

All three produce the same `AgentManifest` — pick your preferred style.

## The HTTP Client

```python
from forgeos_sdk import ForgeOSClient

client = ForgeOSClient(
    base_url="http://localhost:5000",  # or FORGEOS_API_URL env var
    api_key="...",                     # or FORGEOS_API_KEY env var
)

# Lifecycle
agent_id = client.deploy("./agent.yaml")
client.update(agent_id, manifest)
client.stop(agent_id)
client.undeploy(agent_id)

# Invocation
result = client.invoke(agent_id, "Check the inbox now")

# Streaming chat (SSE)
for event in client.chat_stream(agent_id, "Hello!", session_id="s1"):
    if event["type"] == "text_delta":
        print(event["content"], end="", flush=True)

# Queries
agents = client.list(stack="forgeos", department="operations")
agent = client.get(agent_id)
overview = client.overview()

# Events + approvals
client.fire_event("lead.qualified", {"lead_id": "L-123"})
pending = client.list_approvals()
client.approve(request_id, approved_by="jama", notes="LGTM")
```

## The Kernel from the SDK

Agents can check permissions, introspect their own contract, and record audit events through the `Kernel` accessor:

```python
from forgeos_sdk import Kernel

kernel = Kernel.connect()   # auto-detects in-process vs remote

# Check before acting
decision = await kernel.check_tool_call(
    agent_id="my-agent",
    tool_name="email.send",
    tool_input={"to": "customer@example.com"},
    estimated_cost_usd=0.02,
)
if decision.denied:
    raise PermissionError(decision.reason)

# Check if you can call another agent
decision = await kernel.check_a2a_call(
    caller_agent_id="my-agent",
    target_namespace="legal",
    target_name="contract-reviewer",
)

# Introspect your own contract
contract = await kernel.get_contract("my-agent")
budget = contract["metadata"].get("_boundaries", {}).get("budgets")

# Record custom audit events
await kernel.audit("my-agent", "decision_made", {"choice": "approved"})
```

See [the Kernel docs](../architecture/kernel.md) for the full API.

## The CLI

```bash
# Validate a manifest without deploying
forgeos validate ./agent.yaml

# Deploy
forgeos deploy ./agent.yaml

# List
forgeos list

# Invoke
forgeos invoke <agent_id> "Your prompt"

# Undeploy
forgeos undeploy <agent_id>

# Health check
forgeos health
```

The CLI respects `FORGEOS_API_URL` and `FORGEOS_API_KEY` environment variables.

## Manifest Schema

The `AgentManifest` supports two API versions:

| apiVersion | Structure | When to use |
|-----------|-----------|-------------|
| `forgeos/v1` | Flat spec (stack, execution_type, tools as list) | Backward compatibility, simple agents |
| `agentos/v1` | K8s-style (metadata.namespace, spec.runtime, spec.lifecycle, spec.capabilities.a2a, spec.boundaries, spec.governance, spec.dependencies, status) | Full AgentOS features |

Both round-trip through the same `POST /api/platform/agents` endpoint — the SDK flattens v2 manifests to v1 wire format on deploy and preserves v2 fields in `metadata._*` for the kernel to read at runtime.

See [Agent Manifest Reference](../reference/agent-manifest.md) for the complete schema.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `FORGEOS_API_URL` | Backend URL used by client + CLI | `http://localhost:5000` |
| `FORGEOS_API_KEY` | API key (sent as `X-API-Key`) | — |

## Testing

SDK tests live alongside platform tests:

```bash
PYTHONPATH=. python3 -m pytest tests/test_kernel.py tests/test_a2a.py -v
```
