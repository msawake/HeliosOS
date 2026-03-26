"""
AI-assisted agent creation wizard.

Builds a deployable AgentDefinition-shaped proposal from conversation using
an LLM (when configured) or lightweight heuristics (offline / no API keys).

Output is always validated against platform enums before returning to clients.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from stacks.base import STACK_NAMES, ExecutionType, LLMConfig, OwnershipType

logger = logging.getLogger(__name__)

EXEC_VALUES = frozenset(e.value for e in ExecutionType)
OWNERSHIP_VALUES = frozenset(o.value for o in OwnershipType)

WIZARD_SYSTEM = """You are ForgeOS Agent Architect. Help the user design ONE agent for our multi-stack platform.

Stacks (pick exactly one):
- forgeos: Default — single agent, MCP tools, hooks; best for integrations and general assistants.
- crewai: Multiple specialist roles, sequential/parallel crew work, marketing/sales research crews.
- adk: Enterprise — hierarchical workflows, audit checkpoints, compliance-sensitive processes.
- openclaw: File-first agent (SOUL/HEARTBEAT), local automation, inbox/daemon-style assistants.

Execution types (pick exactly one):
- always_on: Long-running loop (monitoring, daemons).
- scheduled: Cron or interval (reports, daily jobs). Set schedule as shorthand like "every 1h" or "every 15m".
- event_driven: React to named events. Set event_triggers as strings e.g. ["email.received","calendar.meeting_ended"].
- reflex: On-demand invocation only (user or API calls the agent when needed).
- autonomous: Goal-directed loop until done — set goal text; optional metadata loop tuning.

Ownership:
- personal: One user; include owner_id if known (or placeholder "demo-user").
- shared: Team / enterprise — maps to SHARED in API. "enterprise" means shared.

Respond with a SINGLE JSON object only (no markdown fences). Schema:
{
  "assistant_message": "<short markdown for the user: summarize understanding + next step>",
  "proposal": <null or object with keys: name, stack, execution_type, ownership, owner_id?, description, department?, goal?, schedule?, event_triggers?, tools?, llm_config?: {chat_model, reasoning_model?, provider}, metadata?, rationale_bullets?: []>,
  "clarifying_questions": ["optional short question", ...],
  "ready_to_deploy": <true only if proposal is complete and confident>
}

Rules:
- name: kebab-case slug, lowercase, no spaces (e.g. lead-qualifier-bot).
- stack must be one of: forgeos, crewai, adk, openclaw.
- execution_type must be one of: always_on, scheduled, event_driven, reflex, autonomous.
- ownership must be personal or shared.
- If scheduled, proposal.schedule should be set (MVP: "every Nm"/"every Nh").
- If event_driven, event_triggers must be non-empty sensible names.
- If autonomous, goal must be non-empty.
- tools: optional list of string tool identifiers (can be empty).
- ready_to_deploy false until user intent + stack + execution + ownership are clear.
- If ambiguous, set proposal null, add 1–3 clarifying_questions, friendly assistant_message.
"""


def llm_router_has_provider(router: Any) -> bool:
    """True when at least one real provider client is configured (not pure simulation)."""
    if router is None:
        return False
    clients = getattr(router, "_clients", None) or {}
    return bool(clients)


def slugify_name(raw: str) -> str:
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:48] or "new-agent"


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse first JSON object from model output (strip optional ``` fences)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```\s*$", "", t, flags=re.I)
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def normalize_proposal(
    raw: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """
    Validate and normalize proposal dict for POST /api/platform/agents.
    Returns (proposal_or_none, warnings).
    """
    ctx = context or {}
    warnings: list[str] = []
    if not raw or not isinstance(raw, dict):
        return None, ["No proposal to normalize"]

    p = dict(raw)
    name = p.get("name") or "new-agent"
    p["name"] = slugify_name(str(name))

    stack = str(p.get("stack", "forgeos")).lower()
    if stack not in STACK_NAMES:
        warnings.append(f"Invalid stack {stack!r} — defaulting to forgeos")
        stack = "forgeos"
    p["stack"] = stack

    ex = str(p.get("execution_type", "reflex")).lower()
    if ex not in EXEC_VALUES:
        warnings.append(f"Invalid execution_type {ex!r} — defaulting to reflex")
        ex = "reflex"
    p["execution_type"] = ex

    own = str(p.get("ownership", "shared")).lower()
    if own in ("enterprise", "team", "org", "company", "shared_team"):
        own = "shared"
        warnings.append("Mapped enterprise/team ownership to shared")
    if own not in OWNERSHIP_VALUES:
        warnings.append(f"Invalid ownership {own!r} — defaulting to shared")
        own = "shared"
    p["ownership"] = own

    if p["ownership"] == "personal":
        oid = p.get("owner_id") or ctx.get("default_owner_id") or "demo-user"
        p["owner_id"] = str(oid)
    else:
        p.pop("owner_id", None)

    if p["execution_type"] == "scheduled" and not p.get("schedule"):
        p["schedule"] = "every 1h"
        warnings.append("Scheduled agent missing schedule — defaulting to every 1h")

    if p["execution_type"] == "event_driven":
        et = p.get("event_triggers") or []
        if not isinstance(et, list):
            et = []
        p["event_triggers"] = [str(x).strip() for x in et if str(x).strip()][:12]
        if not p["event_triggers"]:
            p["event_triggers"] = ["custom.event"]
            warnings.append("event_driven without triggers — placeholder custom.event")

    if p["execution_type"] == "autonomous" and not (p.get("goal") or "").strip():
        p["goal"] = "Complete the user-defined objective."
        warnings.append("autonomous without goal — added generic goal")

    tools = p.get("tools")
    if tools is None:
        p["tools"] = []
    elif isinstance(tools, list):
        p["tools"] = [str(t).strip() for t in tools if str(t).strip()][:20]
    else:
        p["tools"] = []
        warnings.append("tools was not a list — cleared")

    llm = p.get("llm_config")
    if llm is not None and isinstance(llm, dict):
        p["llm_config"] = {
            "chat_model": str(llm.get("chat_model", "claude-sonnet-4-5")),
            "reasoning_model": llm.get("reasoning_model"),
            "provider": str(llm.get("provider", "anthropic")),
        }
        if p["llm_config"]["reasoning_model"] in (None, ""):
            p["llm_config"].pop("reasoning_model", None)
    else:
        p.pop("llm_config", None)

    meta = p.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        p.pop("metadata", None)
        warnings.append("metadata ignored (not an object)")

    for key in ("description", "department", "goal", "schedule"):
        if p.get(key) is not None:
            p[key] = str(p[key]) if p[key] is not None else ""

    p.pop("rationale_bullets", None)
    return p, warnings


def _conversation_text(messages: list[dict[str, Any]]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts).lower()


def heuristic_proposal(
    messages: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """When no LLM keys are set, infer a starter proposal from keywords."""
    ctx = context or {}
    text = _conversation_text(messages)
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            last_user = str(m["content"]).strip()
            break

    stack = "forgeos"
    if any(k in text for k in ("crew", "crewai", "multi-agent", "multiple agents", "specialist")):
        stack = "crewai"
    elif any(k in text for k in ("enterprise", "compliance", "audit trail", "google adk", "adk")):
        stack = "adk"
    elif any(k in text for k in ("openclaw", "soul.md", "heartbeat", "file-first", "local daemon")):
        stack = "openclaw"

    ex = "reflex"
    if any(k in text for k in ("every day", "daily", "cron", "schedule", "hourly", "weekly")):
        ex = "scheduled"
    elif any(k in text for k in ("webhook", "when email", "event", "trigger", "slack message")):
        ex = "event_driven"
    elif any(k in text for k in ("24/7", "always on", "always-on", "monitor continuously", "daemon")):
        ex = "always_on"
    elif any(k in text for k in ("until done", "autonomous", "multi-step goal", "keep going until")):
        ex = "autonomous"

    ownership = "shared"
    owner_id = ctx.get("default_owner_id")
    if any(k in text for k in ("personal", "my inbox", "just for me", "private to me")):
        ownership = "personal"
        owner_id = owner_id or "demo-user"

    name_seed = last_user[:48] if last_user else "assistant"
    name = slugify_name(name_seed)

    schedule = "every 1h" if ex == "scheduled" else ""
    triggers = []
    if ex == "event_driven":
        triggers = ["email.received"] if "email" in text else ["custom.event"]

    goal = ""
    if ex == "autonomous":
        goal = last_user or "Complete the described objective."

    proposal_raw = {
        "name": name,
        "stack": stack,
        "execution_type": ex,
        "ownership": ownership,
        "owner_id": owner_id,
        "description": last_user or "Agent created via AI wizard (heuristic mode).",
        "department": "general",
        "goal": goal or None,
        "schedule": schedule or None,
        "event_triggers": triggers or None,
        "tools": [],
        "llm_config": {"chat_model": "claude-sonnet-4-5", "provider": "anthropic"},
    }
    normalized, warns = normalize_proposal(proposal_raw, context=ctx)

    assistant = (
        "**Offline wizard mode** — no LLM API key detected on the server. "
        "I inferred a starter configuration from your message; add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` "
        "for richer suggestions.\n\n"
        f"Inferred **{normalized['stack']}** / **{normalized['execution_type']}** / **{normalized['ownership']}**. "
        "Review the draft and deploy, or refine in chat."
    )

    return {
        "assistant_message": assistant,
        "proposal": normalized,
        "clarifying_questions": [
            "Who is the owner (team vs personal), and any SLA or schedule?",
            "What external systems or tools should this agent use?",
        ],
        "ready_to_deploy": True,
        "rationale_bullets": ["keyword-based inference"] + warns,
        "_warnings": warns,
        "_mode": "heuristic",
    }


async def run_wizard_turn(
    llm_router: Any | None,
    messages: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    One chat turn: returns assistant_message, proposal (normalized or null),
    clarifying_questions, ready_to_deploy, warnings, mode.
    """
    ctx = context or {}

    if not llm_router_has_provider(llm_router):
        h = heuristic_proposal(messages, ctx)
        h["warnings"] = list(h.pop("_warnings", []))
        h["mode"] = h.pop("_mode", "heuristic")
        return h

    llm_config = LLMConfig(
        chat_model=ctx.get("wizard_model", "claude-sonnet-4-5"),
        provider=str(ctx.get("wizard_provider", "anthropic")),
    )

    api_messages = [
        {"role": "system", "content": WIZARD_SYSTEM},
        *[{"role": m["role"], "content": str(m.get("content", ""))} for m in messages if m.get("content")],
    ]

    try:
        response = await llm_router.chat(llm_config, api_messages)
        raw_text = response.text or ""
    except Exception as e:
        logger.exception("Wizard LLM call failed")
        return {
            "assistant_message": f"Wizard LLM error: {e}",
            "proposal": None,
            "clarifying_questions": [],
            "ready_to_deploy": False,
            "warnings": [str(e)],
            "mode": "error",
        }

    parsed = extract_json_object(raw_text)
    if not parsed:
        return {
            "assistant_message": raw_text
            or "I could not parse a valid JSON reply. Please try again with a clearer goal.",
            "proposal": None,
            "clarifying_questions": ["What is the main outcome this agent should produce?"],
            "ready_to_deploy": False,
            "warnings": ["Model output was not valid JSON"],
            "mode": "llm_unparsed",
        }

    assistant_message = str(parsed.get("assistant_message", "")).strip() or "Here is an updated plan."
    clarifying = parsed.get("clarifying_questions") or []
    if not isinstance(clarifying, list):
        clarifying = []
    clarifying = [str(x) for x in clarifying[:5]]

    ready = bool(parsed.get("ready_to_deploy"))
    proposal_in = parsed.get("proposal")
    proposal_norm = None
    all_warns: list[str] = []

    if proposal_in is not None and isinstance(proposal_in, dict):
        proposal_norm, nwarns = normalize_proposal(proposal_in, context=ctx)
        all_warns.extend(nwarns)
        if ready and proposal_norm is None:
            ready = False
    else:
        ready = False

    bullets = parsed.get("rationale_bullets") or []
    if isinstance(bullets, list):
        all_warns.extend(str(b) for b in bullets[:10])

    return {
        "assistant_message": assistant_message,
        "proposal": proposal_norm,
        "clarifying_questions": clarifying,
        "ready_to_deploy": ready and proposal_norm is not None,
        "warnings": all_warns,
        "mode": "llm",
    }
