# Content Pipeline: Raw vs ForgeOS Governed

Two files, same pipeline, completely different risk profile.

```
agent_raw.py  →  92 lines, 0 runtime checks, 0 safety guarantees
agent.py      → 629 lines, 12 runtime checks, full governance
```

## Side-by-Side: The Core Pipeline

### WITHOUT ForgeOS (`agent_raw.py`)

```python
async def produce_content(client_id: str, topic: str):
    client = CLIENTS[client_id]

    # ── PRODUCE ──
    draft = await call_llm("gemini-2.5-flash", topic,
        system=f"Brand voice: {client['brand_voice']}")

    # ── REVIEW ──
    review = await call_llm("claude-sonnet", f"Review: {draft}",
        system=f"Compliance: {client['compliance']}")

    print(f"DRAFT: {draft[:200]}...")
    print(f"REVIEW: {review[:200]}...")
```

**That's it.** No isolation, no budget, no HITL, no audit. Ship it? Sure — until PharmaCo's clinical data shows up in FinTech's blog post.

---

### WITH ForgeOS (`agent.py`)

```python
async def produce_content(client_id: str, topic: str):
    client = CLIENTS[client_id]
    namespace = client["namespace"]

    # ① Client isolation — pharma can't see fintech data
    data_decision = await runtime.check_data(namespace)
    if data_decision.action == "deny":
        return {"error": f"Access denied: {data_decision.reason}"}

    # ② Budget check — this client has $1500/month cap
    budget = await runtime.budget()
    if budget.remaining_usd < 0.10:
        return {"error": "Budget exhausted"}

    # ③ Reserve cost before spending
    ticket = await runtime.reserve(estimated_cost_usd=0.50)

    # ④ Tool gate — no AI images for pharma (HIPAA)
    img_decision = await runtime.check_tool("image.generate")
    image_allowed = img_decision.action != "deny"

    # ── PRODUCE (Gemini Flash) ──
    draft = await call_producer(topic, system=producer_system)

    # ⑤ Record what was generated
    await runtime.audit("content.draft_created", {
        "client": client_id, "topic": topic, "tokens": draft["tokens"]
    })

    # ⑥ A2A check — producer can only call THIS client's editor
    a2a_decision = await runtime.check_a2a(namespace, "editor")
    if a2a_decision.action == "deny":
        return {"error": "Cannot call editor across client boundary"}

    # ── REVIEW (Claude Sonnet) ──
    # ⑦ Can editor access brand guidelines?
    await runtime.check_tool("brand.read_guidelines")

    review = await call_editor(draft, system=editor_system)

    # ⑧ Record compliance review outcome
    await runtime.audit("content.compliance_reviewed", {
        "client": client_id, "outcome": outcome, "risk_level": risk_level
    })

    # ⑨ HITL — human must approve regulated content
    if client["hitl_required"] or risk_level in ("HIGH", "CRITICAL"):
        await runtime.ask_human(
            namespace=namespace,
            name="editorial-lead",
            question=f"Review needed: {topic}\nRisk: {risk_level}",
            response_type="choice",
            options=[
                {"value": "approve", "label": "Approve for publication"},
                {"value": "revise",  "label": "Send back for revision"},
                {"value": "reject",  "label": "Reject — do not publish"},
            ],
            priority="high" if risk_level == "CRITICAL" else "medium",
        )

    # ⑩ Finalize budget with actual cost
    await runtime.commit(ticket, actual_cost_usd=total_cost)

    # ⑪ Save progress (crash recovery)
    await runtime.checkpoint({
        "client": client_id, "topic": topic, "outcome": outcome
    })

    # ⑫ Final audit record
    await runtime.audit("content.pipeline_completed", {
        "client": client_id, "outcome": outcome, "cost_usd": total_cost,
        "hitl_required": client["hitl_required"]
    })
```

---

## What Each Check Prevents

| # | Runtime Call | Without It | Real-World Consequence |
|---|------------|------------|----------------------|
| ① | `check_data()` | Any agent reads any client's data | Pfizer's trial data in Novartis blog |
| ② | `budget()` | No spending limits | $500 client burns $2000 in one day |
| ③ | `reserve()` | Cost invisible until invoice | Month-end surprise: 3x over budget |
| ④ | `check_tool()` | All tools available to all clients | AI-generated fake lab results for pharma |
| ⑤ | `audit()` | No record of generation | "Who created this?" → silence |
| ⑥ | `check_a2a()` | Any producer calls any editor | Client A's strategy leaks to Client B |
| ⑦ | `check_tool()` | Editor accesses anything | Editor reads wrong client's brand guide |
| ⑧ | `audit()` | No review proof | Regulators ask for compliance evidence |
| ⑨ | `ask_human()` | Auto-published | AI publishes "cures cancer" claim → FDA |
| ⑩ | `commit()` | Budget not tracked | No per-client cost accounting |
| ⑪ | `checkpoint()` | Crash = redo from scratch | 50 pieces lost, double the cost |
| ⑫ | `audit()` | No completion record | Client audit: "prove you reviewed this" |

## Run Both

```bash
# Raw (no governance):
PYTHONPATH=. ATLAS_GATEWAY_URL=... ATLAS_GATEWAY_KEY=... \
  python3 examples/content-ops/agent_raw.py

# Governed (12 runtime checks):
PYTHONPATH=. ATLAS_GATEWAY_URL=... ATLAS_GATEWAY_KEY=... \
  python3 examples/content-ops/agent.py
```

## The Numbers

|  | Raw | ForgeOS Governed |
|--|-----|-----------------|
| Lines of code | 92 | 629 |
| Runtime checks | 0 | 12 per piece |
| Client isolation | None | Namespace boundary |
| Budget control | None | Per-client caps |
| Human review | None | HITL for regulated |
| Audit trail | None | Every action recorded |
| Crash recovery | Start over | Resume from checkpoint |
| Tool restrictions | All allowed | Per-client allowlists |
| A2A boundaries | Open | Scoped to client |
| Cost to add governance | — | $0 (same LLM calls, governance is metadata) |

**The LLM calls are identical.** ForgeOS adds the governance layer around them — namespace isolation, budget, HITL, audit — without changing the AI logic.
