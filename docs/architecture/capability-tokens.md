# Capability Tokens

Runtime grants that authorize specific operations without modifying static ACLs. A capability token is an opaque handle that says "agent X may do Y to Z until time T."

## The Problem They Solve

Static ACLs (declared in the agent manifest) can't express:

- **Temporary access**: "for this task only, let the sales agent read finance data"
- **Delegated authority**: "the orchestrator grants the worker permission to call the CFO"
- **Revocable grants**: "access is valid for 5 minutes, then expires"

Capability tokens handle all three. They're positive authority — when a valid token is presented, the action is allowed regardless of ACL. When no token is presented, the normal ACL path runs.

## Token Structure

```python
@dataclass
class CapabilityToken:
    id: str             # opaque 128-bit hex handle
    subject: str        # PID of the agent this token was issued to
    target: str         # what it grants access to ("finance/cfo")
    verb: str           # the operation ("a2a.invoke", "tool.call", "*")
    issued_at: str      # ISO timestamp
    expires_at: str | None  # None = no expiry
    metadata: dict      # arbitrary context (reason, issuer, etc.)
```

## Three Checks

When a token is presented, the kernel checks:

1. **Does the token exist?** (revocation = delete the entry)
2. **Has it expired?** (current time > `expires_at`)
3. **Does (subject, target, verb) match?** (the requested action)

If all three pass → action allowed, ACL check skipped.
If any fail → falls back to normal ACL check (non-destructive).

## Using Capability Tokens

### From Agent Code (SDK Runtime)

```python
from forgeos_sdk import runtime

# Request a capability: "I need to call the finance reviewer for 5 minutes"
cap = await runtime.request_capability(
    target="finance/finance-reviewer",
    verb="a2a.invoke",
    ttl=300,  # 5 minutes
    metadata={"reason": "need budget approval for campaign"},
)
print(f"Token: {cap.id}, expires: {cap.expires_at}")

# List active capabilities
caps = await runtime.list_capabilities()
for c in caps:
    print(f"  {c.target} ({c.verb}) — expires {c.expires_at}")

# Revoke when done
await runtime.revoke_capability(cap.id)
```

### From Platform Code (Kernel)

```python
# Issue
token = kernel.issue_capability(
    subject="sales/sdr-01",
    target="finance/cfo",
    verb="a2a.invoke",
    ttl_seconds=600,
)

# Check
authorized = kernel.authorize_capability(
    token_id=token.id,
    subject="sales/sdr-01",
    target="finance/cfo",
    verb="a2a.invoke",
)

# Revoke
kernel.revoke_capability(token.id)
```

## CapabilityManager API

```python
class CapabilityManager:
    def issue(*, subject, target, verb="*", ttl_seconds=None, metadata=None) -> CapabilityToken
    def revoke(token_id: str) -> bool
    def authorize(*, token_id, subject, target, verb) -> bool
    def get(token_id: str) -> CapabilityToken | None
    def list_for_subject(subject: str) -> list[CapabilityToken]
```

## Storage

Currently in-memory (`dict[str, CapabilityToken]`). Swapping to a durable `Store[CapabilityToken]` is a one-line change on the store protocol — planned for when multi-tenant-across-orgs becomes a real requirement.

## Integration with A2A

The A2A handler consults capabilities before falling back to ACL:

1. Caller presents a capability token → `CapabilityManager.authorize()` → if valid, allow
2. No token → fall back to `spec.capabilities.a2a.canBeCalledBy` ACL check
3. No ACL match → same-namespace default rule

## Security Properties

- **Unsigned, opaque handles**: 128-bit hex IDs. Not JWTs — no signature verification overhead. Secure because the store is kernel-local (not transmitted across trust boundaries).
- **Positive authority**: tokens grant access, they don't restrict it. An agent without a token still gets normal ACL evaluation.
- **Revocation is instant**: delete the entry, next check fails.
- **Expiry is passive**: no background cleanup needed — expired tokens fail the time check.

## Source Files

- `src/platform/capabilities.py` — CapabilityToken, CapabilityManager, CapabilityStore protocol
- `src/platform/kernel.py` — `issue_capability()`, `revoke_capability()`, `authorize_capability()`
- `src/forgeos_sdk/runtime.py` — `request_capability()`, `revoke_capability()`, `list_capabilities()`
- `tests/test_platform_capabilities.py` — Token lifecycle tests
- `tests/test_a2a_capability_and_contract.py` — A2A integration with capabilities
