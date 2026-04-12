"""
Multi-turn Wizard Agent for high-quality agent creation.

Implements a Gather → Design → Verify → Decide loop using Opus with
tool access to the platform registry, tool inventory, and stack knowledge.
Each wizard turn runs up to 3 internal refinement loops before responding.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from stacks.base import LLMConfig, STACK_NAMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wizard tools — let the wizard query the platform during design
# ---------------------------------------------------------------------------

WIZARD_TOOLS = [
    {
        "name": "wizard__list_existing_agents",
        "description": "List all currently deployed agents with their name, stack, execution_type, and description. Use this to check for duplicates and understand what already exists.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wizard__list_available_tools",
        "description": "List all tools the new agent could use. Returns company tools (event bus, HITL, knowledge, metrics), platform tools (CRM, HTTP, ads, MLS, insurance, GitHub, messaging), and MCP tools (Google Workspace, Slack, Stripe, Postgres).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wizard__get_stack_info",
        "description": "Get detailed info about a specific stack: what it's best for, scaffold files it generates, system prompt patterns, and example agents.",
        "input_schema": {
            "type": "object",
            "properties": {"stack": {"type": "string", "enum": ["forgeos", "crewai", "adk", "openclaw"]}},
            "required": ["stack"],
        },
    },
    {
        "name": "wizard__evaluate_proposal",
        "description": "Self-evaluate an agent proposal for quality. Checks: system prompt references real tools, execution type matches use case, all required fields populated, prompt is specific not generic. Returns score (1-10) and issues list.",
        "input_schema": {
            "type": "object",
            "properties": {"proposal": {"type": "object"}},
            "required": ["proposal"],
        },
    },
    {
        "name": "wizard__search_skills",
        "description": "Search the skills library (233 reusable .md knowledge files) for domain expertise relevant to the agent being designed. Returns skill name, description, and domain. Use this to ground the agent's system prompt in real-world best practices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (e.g., 'sales', 'compliance', 'security', 'marketing')"},
                "domain": {"type": "string", "description": "Filter by domain: engineering, marketing-skill, c-level-advisor, finance, project-management, business-growth, etc."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wizard__get_skill",
        "description": "Read the full content of a specific skill by name. Use this to incorporate domain expertise into the agent's system prompt.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name (from search results)"}},
            "required": ["name"],
        },
    },
    {
        "name": "wizard__list_skill_domains",
        "description": "List all skill domains with counts. Helps understand what knowledge areas are available.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "wizard__search_mcps",
        "description": "Search the MCP registry (4,500+ MCP server packages) for external integrations. Find MCP servers for Gmail, Slack, databases, APIs, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword (e.g., 'gmail', 'slack', 'database', 'stripe')"},
                "category": {"type": "string", "description": "Filter by category (e.g., 'communication', 'cloud-platforms', 'data-platforms')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wizard__get_mcp_package",
        "description": "Get full details and connection config for a specific MCP server package.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Package name (from search results)"}},
            "required": ["name"],
        },
    },
    {
        "name": "wizard__list_mcp_categories",
        "description": "List all MCP registry categories with package counts. Helps understand what external integrations are available.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# ---------------------------------------------------------------------------
# Stack knowledge base
# ---------------------------------------------------------------------------

STACK_INFO = {
    "forgeos": {
        "name": "ForgeOS Native",
        "best_for": "Single-agent tasks, API integrations, monitoring, general assistants. The default choice when you don't need multi-agent coordination or file-first config.",
        "scaffold_files": ["agent.py", "tools.py", "prompts/system.md", "config.yaml"],
        "system_prompt_pattern": "Direct role statement. Tool usage instructions with specific tool names. Output format spec. Constraints and escalation rules.",
        "example_agents": ["uptime-sentinel (always_on, monitors APIs)", "email-drafter (reflex, on-demand)", "nightly-lead-scoring (scheduled, midnight batch)"],
        "strengths": "Fastest, lightest, direct tool executor access, production-ready",
        "weaknesses": "Single agent only, no multi-role coordination",
    },
    "crewai": {
        "name": "CrewAI",
        "best_for": "Multi-specialist tasks where different roles collaborate: research + analysis + writing, content production, complex reports requiring multiple perspectives.",
        "scaffold_files": ["agents.py", "tasks.py", "crew.py", "tools.py", "config.yaml"],
        "system_prompt_pattern": "Role/Goal/Backstory pattern. Crew coordination instructions. Task delegation guidance.",
        "example_agents": ["daily-pipeline-report (scheduled, crew of researcher+analyst+writer)", "research-assistant (reflex, deep multi-source research)", "full-sales-cycle-runner (autonomous, multi-step outbound)"],
        "strengths": "Multi-role orchestration, parallel task execution, crew memory",
        "weaknesses": "Heavier than ForgeOS, requires CrewAI SDK for real crew mode",
    },
    "adk": {
        "name": "Google ADK",
        "best_for": "Compliance-heavy, regulated, audit-trail processes. Legal review, financial operations, fraud detection, anything requiring checkpoints and human approval gates.",
        "scaffold_files": ["agent.py", "workflow.py", "tools.py", "prompts/system_prompt.txt", "config.yaml"],
        "system_prompt_pattern": "Enterprise agent with audit trail instructions. Checkpoint after each step. Human escalation for high-risk actions.",
        "example_agents": ["compliance-monitor (always_on, scans for PII/violations)", "contract-review-kickoff (event_driven, legal workflow)", "fraud-detection-alert (event_driven, transaction investigation)"],
        "strengths": "Audit checkpoints, enterprise workflow patterns, regulated processes",
        "weaknesses": "SDK is stub — runs through LLM router, no real ADK integration yet",
    },
    "openclaw": {
        "name": "OpenClaw",
        "best_for": "File-first agent definition. Personal daemons, inbox automation, agents whose behavior non-technical users configure by editing markdown files (SOUL.md, HEARTBEAT.md).",
        "scaffold_files": ["SOUL.md", "IDENTITY.md", "HEARTBEAT.md", "SKILLS/default.yaml", "MEMORY/long-term.md", "config.yaml", "gateway.sh"],
        "system_prompt_pattern": "ReAct loop: Think → Act → Observe → Repeat. SOUL defines personality. HEARTBEAT defines schedule. MEMORY persists state.",
        "example_agents": ["inbox-triage (always_on, email classification)", "knowledge-curator (always_on, document indexing)", "code-review-bot (reflex, PR quality checks)"],
        "strengths": "Human-editable config (markdown), persistent memory folder, ReAct pattern",
        "weaknesses": "Gateway runtime is stub, MEMORY/ not yet auto-populated",
    },
}

# ---------------------------------------------------------------------------
# Grounded system prompt for the wizard agent
# ---------------------------------------------------------------------------

WIZARD_SYSTEM_V2 = """You are the ForgeOS Agent Architect, an expert at designing production-quality AI agents.

You have access to 7 tools that let you query the platform. USE THEM before designing any agent:
1. wizard__list_existing_agents — check what already exists (avoid duplicates)
2. wizard__list_available_tools — see what tools the agent can use (reference REAL tool names)
3. wizard__get_stack_info — understand each stack's strengths before choosing
4. wizard__evaluate_proposal — self-check your proposal quality before presenting
5. wizard__search_skills — search 233 reusable knowledge skills for domain expertise
6. wizard__get_skill — read a skill's full content to incorporate into the agent's system prompt
7. wizard__list_skill_domains — see available knowledge domains (engineering, marketing, finance, etc.)
8. wizard__search_mcps — search 4,500+ MCP server packages for external integrations (Gmail, Slack, DBs, APIs)
9. wizard__get_mcp_package — get connection config for an MCP server
10. wizard__list_mcp_categories — browse MCP categories (communication, cloud, data, etc.)

## YOUR PROCESS (follow every time):

STEP 1 — GATHER: Call wizard__list_available_tools to know what's available. Call wizard__list_existing_agents to check for duplicates. Call wizard__get_stack_info for the stack you're considering. Call wizard__search_skills to find relevant domain expertise (e.g., search "sales" for a sales agent, "compliance" for an audit agent). Read the most relevant skill with wizard__get_skill and incorporate its best practices into your system prompt.

STEP 2 — DESIGN: Create a complete agent proposal with ALL fields. Write a detailed system prompt (200-400 words) that:
- Starts with "You are {agent-name}, a {role description}."
- References ACTUAL tool names from the tool list (e.g., "Use company__search_knowledge to...", not generic "search the knowledge base")
- Specifies the execution pattern (polling interval for always_on, cron for scheduled, event name for event_driven)
- Defines output format
- Lists constraints and escalation rules
- Is specific to the domain, not generic

STEP 3 — VERIFY: Call wizard__evaluate_proposal with your proposal. If score < 8, fix the issues and re-evaluate.

STEP 4 — PRESENT: Return the final proposal to the user.

## STACKS (choose ONE):
- **forgeos** — Simple single-agent tasks, integrations, monitoring (DEFAULT)
- **crewai** — Multi-role collaboration (research + writing + review)
- **adk** — Compliance, audit trails, regulated workflows
- **openclaw** — File-first config, personal daemons, non-technical users edit SOUL.md

## EXECUTION TYPES (choose ONE):
- **always_on** — Persistent daemon with polling interval (metadata.loop_interval_seconds)
- **scheduled** — Cron-based (schedule field, e.g., "0 7 * * *" for 7 AM daily)
- **event_driven** — Triggered by named events (event_triggers list, e.g., ["email.received"])
- **reflex** — On-demand, invoked by user or parent agent
- **autonomous** — Goal-directed loop with max_iterations (goal field required)

## OUTPUT FORMAT (strict JSON):
{
  "assistant_message": "markdown explanation of your design",
  "proposal": {
    "name": "kebab-case-name",
    "stack": "forgeos|crewai|adk|openclaw",
    "execution_type": "always_on|scheduled|event_driven|reflex|autonomous",
    "ownership": "personal|shared",
    "owner_id": "user-id (for personal only)",
    "description": "one-line description",
    "system_prompt": "FULL 200-400 word system prompt",
    "department": "category",
    "schedule": "cron expression (for scheduled)",
    "event_triggers": ["event.name"] (for event_driven),
    "goal": "completion criteria (for autonomous)",
    "tools": ["actual__tool__names"],
    "llm_config": {"chat_model": "gpt-4o", "provider": "openai"},
    "metadata": {}
  },
  "clarifying_questions": [],
  "ready_to_deploy": true/false
}

CRITICAL RULES:
1. Always use your tools FIRST, then design. Never guess tool names — look them up.
2. Never skip the evaluate step.
3. NEVER ask "want me to proceed?" or "shall I continue?" — just DO IT. Produce the full JSON proposal.
4. Your response MUST be valid JSON matching the output format above. No markdown outside the JSON.
5. If the user has given enough info to design the agent, produce the proposal immediately. Do not ask unnecessary clarifying questions."""


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_wizard_tool(
    tool_name: str,
    tool_input: dict,
    platform_registry=None,
    tool_executor=None,
) -> dict:
    """Execute a wizard tool and return the result."""

    if tool_name == "wizard__list_existing_agents":
        if not platform_registry:
            return {"agents": [], "count": 0}
        agents = platform_registry.list_all()
        return {
            "count": len(agents),
            "agents": [
                {"name": a.name, "stack": a.stack, "execution_type": a.execution_type.value,
                 "description": a.description[:100]}
                for a in agents[:30]
            ],
        }

    if tool_name == "wizard__list_available_tools":
        tools = []
        if tool_executor:
            if hasattr(tool_executor, "get_custom_tool_definitions"):
                for t in tool_executor.get_custom_tool_definitions():
                    tools.append({"name": t["name"], "description": t.get("description", "")[:80]})
            if hasattr(tool_executor, "get_platform_tool_definitions"):
                for t in tool_executor.get_platform_tool_definitions():
                    tools.append({"name": t["name"], "description": t.get("description", "")[:80]})
            if hasattr(tool_executor, "get_mcp_tool_definitions"):
                for t in tool_executor.get_mcp_tool_definitions():
                    tools.append({"name": t["name"], "description": t.get("description", "")[:80]})
        return {"count": len(tools), "tools": tools}

    if tool_name == "wizard__get_stack_info":
        stack = tool_input.get("stack", "forgeos")
        info = STACK_INFO.get(stack)
        if not info:
            return {"error": f"Unknown stack: {stack}. Options: {list(STACK_INFO.keys())}"}
        return info

    if tool_name == "wizard__evaluate_proposal":
        proposal = tool_input.get("proposal", {})
        return _evaluate_proposal(proposal)

    # Skill registry tools
    if tool_name in ("wizard__search_skills", "wizard__get_skill", "wizard__list_skill_domains"):
        return _handle_skill_tool(tool_name, tool_input)

    # MCP registry tools
    if tool_name in ("wizard__search_mcps", "wizard__get_mcp_package", "wizard__list_mcp_categories"):
        return _handle_mcp_tool(tool_name, tool_input)

    return {"error": f"Unknown wizard tool: {tool_name}"}


# Lazy-loaded skill registry singleton
_skill_registry = None

def _get_skill_registry():
    global _skill_registry
    if _skill_registry is None:
        from src.platform.skill_registry import SkillRegistry
        _skill_registry = SkillRegistry()
        count = _skill_registry.index()
        logger.info("Skill registry loaded: %d skills", count)
    return _skill_registry


def _handle_skill_tool(tool_name: str, tool_input: dict) -> dict:
    """Handle skill-related wizard tools."""
    registry = _get_skill_registry()

    if tool_name == "wizard__search_skills":
        query = tool_input.get("query", "")
        domain = tool_input.get("domain")
        if not query:
            return {"error": "query is required"}
        results = registry.search(query, domain=domain, limit=10)
        return {"count": len(results), "skills": results}

    if tool_name == "wizard__get_skill":
        name = tool_input.get("name", "")
        if not name:
            return {"error": "name is required"}
        skill = registry.get(name)
        if not skill:
            return {"error": f"Skill '{name}' not found. Use wizard__search_skills to find available skills."}
        return skill

    if tool_name == "wizard__list_skill_domains":
        domains = registry.get_domains()
        return {"total_skills": registry.count(), "domains": domains}

    return {"error": f"Unknown skill tool: {tool_name}"}


# Lazy-loaded MCP registry singleton
_mcp_registry = None

def _get_mcp_registry():
    global _mcp_registry
    if _mcp_registry is None:
        from src.platform.mcp_registry import MCPRegistry
        _mcp_registry = MCPRegistry()
        count = _mcp_registry.index()
        logger.info("MCP registry loaded: %d packages", count)
    return _mcp_registry


def _handle_mcp_tool(tool_name: str, tool_input: dict) -> dict:
    """Handle MCP registry wizard tools."""
    registry = _get_mcp_registry()

    if tool_name == "wizard__search_mcps":
        query = tool_input.get("query", "")
        category = tool_input.get("category")
        if not query:
            return {"error": "query is required"}
        results = registry.search(query, category=category, limit=10)
        return {"count": len(results), "packages": results}

    if tool_name == "wizard__get_mcp_package":
        name = tool_input.get("name", "")
        if not name:
            return {"error": "name is required"}
        pkg = registry.get_package(name)
        if not pkg:
            return {"error": f"Package '{name}' not found. Use wizard__search_mcps to find packages."}
        return pkg

    if tool_name == "wizard__list_mcp_categories":
        categories = registry.get_categories()
        return {"total_packages": registry.count(), "categories": categories[:30]}

    return {"error": f"Unknown MCP tool: {tool_name}"}


def _evaluate_proposal(proposal: dict) -> dict:
    """Score a proposal 1-10 and list issues."""
    issues = []
    score = 10

    if not proposal.get("name"):
        issues.append("Missing name")
        score -= 2
    if not proposal.get("stack") or proposal["stack"] not in STACK_NAMES:
        issues.append(f"Invalid or missing stack (got: {proposal.get('stack')})")
        score -= 2
    if not proposal.get("execution_type"):
        issues.append("Missing execution_type")
        score -= 2
    if not proposal.get("description"):
        issues.append("Missing description")
        score -= 1

    # System prompt quality
    sp = proposal.get("system_prompt", "")
    if not sp:
        issues.append("No system_prompt — this is critical, agent won't know what to do")
        score -= 4
    elif len(sp) < 200:
        issues.append(f"System prompt too short ({len(sp)} chars) — aim for 200-400 words")
        score -= 2
    else:
        # Check if it references actual tool names
        tools = proposal.get("tools", [])
        tools_referenced = sum(1 for t in tools if t in sp)
        if tools and tools_referenced == 0:
            issues.append("System prompt doesn't reference any of the assigned tool names")
            score -= 2
        if "You are" not in sp:
            issues.append("System prompt should start with 'You are {name}, a {role}.'")
            score -= 1

    # Execution-type specific
    et = proposal.get("execution_type", "")
    if et == "scheduled" and not proposal.get("schedule"):
        issues.append("Scheduled agent missing 'schedule' field (e.g., '0 7 * * *')")
        score -= 2
    if et == "event_driven" and not proposal.get("event_triggers"):
        issues.append("Event-driven agent missing 'event_triggers' list")
        score -= 2
    if et == "autonomous" and not proposal.get("goal"):
        issues.append("Autonomous agent missing 'goal' field")
        score -= 2
    if et == "always_on":
        meta = proposal.get("metadata", {})
        if not meta.get("loop_interval_seconds"):
            issues.append("Always-on agent should have metadata.loop_interval_seconds")
            score -= 1

    if not proposal.get("tools"):
        issues.append("No tools assigned — agent can't take actions")
        score -= 1

    score = max(1, min(10, score))
    return {"score": score, "issues": issues, "verdict": "ready" if score >= 8 else "needs_improvement"}


# ---------------------------------------------------------------------------
# Main wizard turn function (replaces old run_wizard_turn)
# ---------------------------------------------------------------------------

async def run_wizard_turn(
    llm_router,
    messages: list[dict],
    context: dict | None = None,
    platform_registry=None,
    tool_executor=None,
) -> dict:
    """
    One wizard chat turn with internal agent loop.

    The wizard:
    1. Receives user message + conversation history
    2. Calls LLM with wizard tools
    3. If LLM uses tools → execute them, feed results back, loop
    4. Extracts final JSON response with proposal
    5. Normalizes and returns
    """
    ctx = context or {}

    # Check if we have a real LLM provider
    if not llm_router or not (hasattr(llm_router, '_clients') and llm_router._clients):
        from src.platform.agent_wizard_planner import heuristic_proposal
        h = heuristic_proposal(messages, ctx)
        h["warnings"] = list(h.pop("_warnings", []))
        h["mode"] = h.pop("_mode", "heuristic")
        return h

    llm_config = LLMConfig(
        chat_model=ctx.get("wizard_model", "claude-opus-4-6"),
        provider=str(ctx.get("wizard_provider", "anthropic")),
    )

    # Build messages: system + conversation history + format reminder
    conv_messages = [
        {"role": m["role"], "content": str(m.get("content", ""))}
        for m in messages if m.get("content")
    ]

    # After 2+ turns, inject a format reminder so Opus doesn't go conversational
    format_reminder = ""
    if len(conv_messages) >= 3:
        format_reminder = (
            "\n\n[SYSTEM REMINDER: You MUST respond with valid JSON matching the output schema. "
            "Do NOT ask 'want me to proceed?' — just produce the proposal. "
            "Use your tools NOW if you haven't already, then output the JSON with "
            "assistant_message, proposal (with system_prompt), and ready_to_deploy.]"
        )
        # Append reminder to the last user message
        conv_messages[-1]["content"] += format_reminder

    # Edit mode: inject existing agent config as preamble so the wizard
    # knows it's refining an existing agent, not creating from scratch.
    if ctx.get("mode") == "edit" and ctx.get("existing_agent"):
        agent = ctx["existing_agent"]
        tools_str = ", ".join(agent.get("tools", [])) or "none"
        edit_preamble = {
            "role": "user",
            "content": (
                f"I want to EDIT an existing deployed agent. Here is its current configuration:\n\n"
                f"Name: {agent.get('name', '?')}\n"
                f"Stack: {agent.get('stack', '?')}\n"
                f"Execution type: {agent.get('execution_type', '?')}"
                f" (schedule: {agent.get('schedule') or 'none'})\n"
                f"Tools: {tools_str}\n"
                f"Description: {agent.get('description', '')}\n"
                f"Department: {agent.get('department', '')}\n"
                f"System prompt: {(agent.get('system_prompt') or '')[:500]}\n"
                f"LLM: {agent.get('llm_config', {}).get('chat_model', '?')} "
                f"({agent.get('llm_config', {}).get('provider', '?')})\n\n"
                f"Please modify this agent based on my next message. "
                f"Keep ALL existing fields unless I specifically ask to change them. "
                f"Return a complete updated proposal."
            ),
        }
        conv_messages.insert(0, edit_preamble)

    api_messages = [
        {"role": "system", "content": WIZARD_SYSTEM_V2},
        *conv_messages,
    ]

    # Agent loop — let the wizard use tools
    max_tool_turns = 100
    for turn in range(max_tool_turns):
        try:
            response = await llm_router.chat(llm_config, api_messages, tools=WIZARD_TOOLS)
        except Exception as e:
            logger.exception("Wizard LLM call failed")
            return {
                "assistant_message": f"[Error] {e}",
                "proposal": None,
                "clarifying_questions": [],
                "ready_to_deploy": False,
                "warnings": [str(e)],
                "mode": "error",
            }

        # No tool calls — we have the final response
        if not response.has_tool_calls:
            return _parse_wizard_response(response.text or "", ctx)

        # Build assistant message with tool_use blocks
        assistant_content = []
        if response.text:
            assistant_content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            assistant_content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        api_messages.append({"role": "assistant", "content": assistant_content})

        # Execute wizard tools
        tool_results = []
        for tc in response.tool_calls:
            logger.info("Wizard tool call: %s", tc.name)
            result = _handle_wizard_tool(
                tc.name, tc.input,
                platform_registry=platform_registry,
                tool_executor=tool_executor,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result),
            })
        api_messages.append({"role": "user", "content": tool_results})

    # Exhausted turns
    return {
        "assistant_message": "The wizard used all its tool turns. Please try a simpler request.",
        "proposal": None,
        "clarifying_questions": [],
        "ready_to_deploy": False,
        "warnings": ["Max wizard tool turns reached"],
        "mode": "llm",
    }


def _parse_wizard_response(raw_text: str, ctx: dict) -> dict:
    """Parse the wizard's final text response into structured output."""
    from src.platform.agent_wizard_planner import extract_json_object, normalize_proposal

    parsed = extract_json_object(raw_text)
    if not parsed:
        # No JSON found — this is a conversational response (e.g., greeting,
        # clarifying question, or freeform advice). Return as a normal chat
        # turn with no proposal. NOT an error.
        return {
            "assistant_message": raw_text or "I couldn't generate a valid response. Please try again.",
            "proposal": None,
            "clarifying_questions": [],
            "ready_to_deploy": False,
            "warnings": [],
            "mode": "llm",
        }

    assistant_message = str(parsed.get("assistant_message", "")).strip() or "Here is the agent design."
    clarifying = parsed.get("clarifying_questions") or []
    if not isinstance(clarifying, list):
        clarifying = []
    clarifying = [str(x) for x in clarifying[:5]]

    ready = bool(parsed.get("ready_to_deploy"))
    proposal_in = parsed.get("proposal")
    proposal_norm = None
    all_warns: list[str] = []

    if proposal_in and isinstance(proposal_in, dict):
        proposal_norm, nwarns = normalize_proposal(proposal_in, context=ctx)
        all_warns.extend(nwarns)
        if ready and proposal_norm is None:
            ready = False
    else:
        ready = False

    return {
        "assistant_message": assistant_message,
        "proposal": proposal_norm,
        "clarifying_questions": clarifying,
        "ready_to_deploy": ready and proposal_norm is not None,
        "warnings": all_warns,
        "mode": "llm",
    }
