# ADK Original vs Helios OS: Side-by-Side Comparison

The customer-service agent from [google/adk-samples](https://github.com/google/adk-samples/tree/main/python/agents/customer-service) compared with the same agent running under Helios OS governance.

---

## 1. Agent Definition

### ADK Original (`customer_service/agent.py`)

```python
from google.adk import Agent
from .config import Config
from .prompts import GLOBAL_INSTRUCTION, INSTRUCTION
from .shared_libraries.callbacks import after_tool, before_agent, before_tool, rate_limit_callback
from .tools.tools import (
    access_cart_information, approve_discount, check_product_availability,
    generate_qr_code, get_available_planting_times, get_product_recommendations,
    modify_cart, schedule_planting_service, send_call_companion_link,
    send_care_instructions, sync_ask_for_approval, update_salesforce_crm,
)

configs = Config()

root_agent = Agent(
    model=configs.agent_settings.model,           # Model set in config file
    global_instruction=GLOBAL_INSTRUCTION,         # System prompt in code
    instruction=INSTRUCTION,                       # More instructions in code
    name=configs.agent_settings.name,              # Name in config file
    tools=[                                        # ALL 12 tools — no restrictions
        send_call_companion_link,
        approve_discount,                          # ← No governance on discounts
        sync_ask_for_approval,
        update_salesforce_crm,                     # ← Anyone can update CRM
        access_cart_information,
        modify_cart,
        get_product_recommendations,
        check_product_availability,
        schedule_planting_service,
        get_available_planting_times,
        send_care_instructions,
        generate_qr_code,
    ],
    before_tool_callback=before_tool,              # Custom callback in code
    after_tool_callback=after_tool,                # Custom callback in code
    before_agent_callback=before_agent,            # Custom callback in code
    before_model_callback=rate_limit_callback,     # Rate limit in code
)
```

**What's missing:**
- No budget limits (can spend unlimited on LLM calls)
- No tool ACLs (all 12 tools always available)
- No audit trail (no record of what was called)
- No PII protection (customer data flows freely)
- No human-in-the-loop enforcement (discount logic is just an `if` statement)
- No namespace isolation (any agent can access any data)
- Callbacks are hardcoded in Python — can't be changed without redeploying

---

### Helios OS Version (`examples/adk-agents/customer-service.yaml`)

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: customer-service
  namespace: support                    # ← Namespace isolation
  description: "Customer service agent"
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: gemini-2.5-flash        # ← Model in manifest, not code
    provider: vertex

  tools:                                # All tools declared
    - send_call_companion_link
    - approve_discount
    - sync_ask_for_approval
    - update_salesforce_crm
    - access_cart_information
    - modify_cart
    - get_product_recommendations
    - check_product_availability
    - schedule_planting_service
    - get_available_planting_times
    - send_care_instructions
    - generate_qr_code

  capabilities:                         # ← TOOL ACLs (not in ADK)
    tools:
      allowed:                          # Only these can actually execute
        - send_call_companion_link
        - access_cart_information
        - modify_cart
        - get_product_recommendations
        - check_product_availability
        - schedule_planting_service
        - get_available_planting_times
        - send_care_instructions
        - generate_qr_code
        - memory__*
      denied:                           # These are BLOCKED by the kernel
        - update_salesforce_crm         # ← CRM updates blocked
        - delete_*                      # ← All delete operations blocked

  boundaries:                           # ← BUDGET LIMITS (not in ADK)
    budgets:
      daily_usd: 5.00                  # Max $5/day spend
      per_task_usd: 0.50              # Max $0.50 per conversation
    data:
      pii_policy: mask                 # ← PII MASKING (not in ADK)

  governance:                           # ← GOVERNANCE (not in ADK)
    audit_level: full                  # Every action logged
    human_in_loop:
      - event: approve_discount        # ← HITL enforcement
        approvers: [manager]           # Requires manager approval
        sla_hours: 1                   # Must approve within 1 hour

  system_prompt: |                      # ← Prompt in manifest, not code
    You are Cymbal Home & Garden's AI customer service agent...
```

---

## 2. Tool Governance

### ADK Original: Discount Logic (hardcoded in Python)

```python
# customer_service/tools/tools.py — lines 47-73

MAX_DISCOUNT_RATE = 10

def approve_discount(discount_type: str, value: float, reason: str) -> str:
    # Only check: is value > 10?
    # No audit. No approval flow. No budget check.
    if value > MAX_DISCOUNT_RATE:
        return {"status": "rejected", "message": "discount too large. Must be 10 or less."}
    return {"status": "ok"}
```

```python
# customer_service/shared_libraries/callbacks.py — lines 142-175

def before_tool(tool, args, tool_context):
    # Hardcoded business logic in a callback
    if tool.name == "sync_ask_for_approval":
        amount = args.get("value", None)
        if amount <= MAX_DISCOUNT_RATE:
            return {"status": "approved", "message": "You can approve this; no manager needed."}
    # No audit trail. No budget check. Logic lives in code.
```

**Problems:**
- Discount rules are scattered across `tools.py` and `callbacks.py`
- No audit — nobody knows what discounts were given
- A prompt injection could bypass the `if value > 10` check
- No way to change the policy without redeploying code

### Helios OS: Kernel Enforces Before Tool Executes

```python
# src/platform/agentic_loop.py — lines 428-438
# This code runs BEFORE approve_discount() is ever called

try:
    from src.forgeos_sdk.runtime import runtime as _rt
    if _rt.is_registered and _rt.is_bound:
        decision = await _rt.check_tool(tool_name, tool_input)
        #
        # For "approve_discount":
        #   1. PermissionManager checks: is "approve_discount" in allowed list?
        #      → It's NOT in the allowed list (it's in the denied list)
        #      → KernelDecision(action="deny", reason="Tool 'approve_discount' not in agent's allowed tools")
        #
        # The tool function NEVER EXECUTES. The kernel blocked it.
        #
        if decision.denied:
            return {"error": f"Kernel denied: {decision.reason}"}
except Exception:
    pass
```

**What's different:**
- The kernel blocks `approve_discount` at the platform level — the tool code never runs
- Even if a prompt injection tricks the LLM into calling it, the kernel denies
- The denial is recorded in the audit log with timestamp, agent_id, and reason
- To change the policy, edit the manifest YAML — no code redeploy needed

---

## 3. Rate Limiting

### ADK Original: `time.sleep()` in a Callback

```python
# customer_service/shared_libraries/callbacks.py — lines 39-85

RATE_LIMIT_SECS = 60
RPM_QUOTA = 10

def rate_limit_callback(callback_context, llm_request):
    now = time.time()
    request_count = callback_context.state["request_count"] + 1
    elapsed_secs = now - callback_context.state["timer_start"]

    if request_count > RPM_QUOTA:
        delay = RATE_LIMIT_SECS - elapsed_secs + 1
        if delay > 0:
            time.sleep(delay)          # ← Blocks the thread!
        callback_context.state["timer_start"] = now
        callback_context.state["request_count"] = 1
```

**Problems:**
- `time.sleep()` blocks the entire thread — in a server, this blocks other requests
- Rate limit is per-agent-instance, not per-agent-globally
- No cost tracking — just counts requests, not tokens or dollars
- Hardcoded: 10 RPM, can't change without code change

### Helios OS: Budget Manager (per-agent, persistent)

```yaml
# In the manifest:
boundaries:
  budgets:
    daily_usd: 5.00        # Tracked across ALL invocations, all day
    per_task_usd: 0.50     # Per conversation limit
```

```python
# src/platform/kernel/_facade.py — BudgetManager (line 363)
# Checks cumulative spend from the process table

class BudgetManager:
    def check_budget(self, agent_id, estimated_cost_usd):
        proc = self._process_table.get(agent_id)
        current_spend = proc.resource_usage.dollars  # Cumulative, persisted
        
        if daily_limit and current_spend + estimated_cost_usd > daily_limit:
            return KernelDecision.deny("Daily budget $5.00 exceeded")
        return KernelDecision.allow()
```

**What's different:**
- Tracks actual dollars spent, not just request count
- Persistent across restarts (process table)
- Per-agent AND per-namespace budgets
- No thread blocking — returns DENY instantly
- Visible in fleet dashboard: `GET /api/platform/fleet`

---

## 4. Customer Data / PII

### ADK Original: No Protection

```python
# customer_service/shared_libraries/callbacks.py — lines 88-125

def validate_customer_id(customer_id, session_state):
    # Validates customer_id matches session — but:
    # - Customer name, email, address flow freely to LLM
    # - No masking, no audit of data access
    # - Any tool can read any customer field
    c = Customer.model_validate_json(session_state["customer_profile"])
    if customer_id == c.customer_id:
        return True, None
```

### Helios OS: PII Policy + Namespace Isolation

```yaml
# In the manifest:
boundaries:
  data:
    pii_policy: mask           # Customer PII masked in audit logs
    allowed_namespaces:        # (if set) restricts data access
      - support
```

```python
# src/platform/kernel/_facade.py — DataBoundaryManager (line 663)

class DataBoundaryManager:
    def check_data_access(self, agent_id, target_namespace):
        # Agent in "support" namespace can't access "finance" data
        if target_namespace not in agent_allowed_namespaces:
            return KernelDecision.deny("Namespace boundary violation")
```

---

## 5. Observability

### ADK Original: `logger.info()` Only

```python
# Scattered across tools.py:
logger.info("Sending call companion link to %s", phone_number)
logger.info("Approving a %s discount of %s because %s", ...)
logger.info("Updating Salesforce CRM for customer ID %s", ...)
# No structured audit. No hash chain. No dashboard.
```

### Helios OS: Hash-Chained Audit + Fleet Dashboard

```
Every kernel decision is recorded:

{
  "audit_id": "cc6dc80e-bf2",
  "agent_id": "3cd5d08f-5f4",
  "action": "tool.call",
  "tool": "send_care_instructions",
  "decision": "allow",
  "namespace": "support",
  "timestamp": "2026-05-17T07:42:23Z",
  "prev_hash": "a8f3c2..."          ← Hash chain — tamper-proof
}

Fleet dashboard: GET /api/platform/fleet
{
  "agents": [{
    "name": "customer-service",
    "phase": "running",
    "dollars": 0.08,
    "tokens": 2300,
    "tool_calls": 5,
    "last_heartbeat": "2026-05-17T07:42:06"
  }]
}
```

---

## 6. Deployment

### ADK Original: Python Code + ADK CLI

```bash
# Start the agent locally:
cd customer-service
pip install -e .
adk web                    # Runs on localhost:8080
# No governance. No multi-agent. No persistence.
```

```python
# Deploy to Vertex AI Agent Engine:
# deployment/deploy.py — requires Vertex AI setup
from google.adk.deployment import deploy
deploy(agent=root_agent, ...)
```

### Helios OS: Declarative Manifest

```bash
# Deploy with governance:
forgeos deploy examples/adk-agents/customer-service.yaml

# That one command:
# 1. Validates the manifest (schema check)
# 2. Runs kernel admission (policy check)
# 3. Registers in agent registry
# 4. Creates process table entry
# 5. Wires execution lifecycle
# 6. Agent is live with full governance
```

---

## Summary: What Helios OS Adds

| Capability | ADK Original | Helios OS |
|-----------|-------------|---------|
| **Tool ACLs** | All 12 tools always available | Kernel allows/denies per tool per agent |
| **Budget limits** | `time.sleep()` rate limit | $5/day, $0.50/task — enforced by kernel |
| **Audit trail** | `logger.info()` | Hash-chained audit log, every decision |
| **PII protection** | None | `pii_policy: mask` in manifest |
| **HITL** | `if value > 10` in code | `human_in_loop` in manifest, enforced |
| **Namespace isolation** | None | `namespace: support` — can't access finance |
| **Fleet visibility** | None | Dashboard + `/api/platform/fleet` |
| **Multi-agent governance** | None | Team manifests + A2A ACLs |
| **Configuration** | Python code + config files | YAML manifest (no code changes) |
| **Policy changes** | Redeploy code | Edit YAML, redeploy manifest |
| **Prompt injection defense** | `if` statements in tools | Kernel blocks at platform level, before tool code runs |

### Lines of Code Comparison

| Component | ADK Original | Helios OS |
|-----------|-------------|---------|
| Agent definition | 77 lines Python | 85 lines YAML |
| Tool governance | 175 lines Python (callbacks.py) | 0 lines (kernel handles it) |
| Rate limiting | 47 lines Python | 3 lines YAML (`daily_usd`, `per_task_usd`) |
| PII handling | 0 | 1 line YAML (`pii_policy: mask`) |
| Audit | 0 | 0 (automatic) |
| **Total agent code** | **~300 lines Python** | **85 lines YAML + 0 lines Python** |

The agent's business logic (tools) stays the same. Helios OS replaces the governance code (callbacks, rate limiting, validation) with declarative YAML enforced by the kernel.
