"""
Intelligence agents for the ForgeOS Intelligence Platform.

Defines three specialized agents that query the ontology knowledge graph
to provide ad-hoc business intelligence:

- intel-analyst: Executive-tier analyst that answers business questions
- intel-monitor: Worker-tier monitor that checks for anomalies
- intel-reporter: Worker-tier reporter that generates structured reports
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

ANALYST_SYSTEM_PROMPT = (
    "You are an Intelligence Analyst. Query the ontology to answer business questions. "
    "Use ontology_get_schema first to understand available data. Then use "
    "ontology_query_objects, ontology_get_neighbors, and ontology_aggregate to find "
    "insights. Present findings clearly with data backing every claim.\n\n"
    "Guidelines:\n"
    "- Always start by calling ontology_get_schema to understand what object types "
    "and relationships are available.\n"
    "- Use ontology_query_objects to fetch specific business entities (Customers, Leads, Deals, etc.).\n"
    "- Use ontology_get_neighbors to traverse relationships and understand connections.\n"
    "- Use ontology_aggregate for metrics: counts, sums, averages grouped by properties.\n"
    "- Use ontology_search for free-text lookups when the user mentions a name or keyword.\n"
    "- Cite specific data in your answers. Never speculate without data.\n"
    "- Structure answers with clear sections, bullet points, and data tables when appropriate.\n"
    "- If a question cannot be answered with available data, say so explicitly and suggest "
    "what data would be needed."
)

MONITOR_SYSTEM_PROMPT = (
    "You are an Intelligence Monitor. Check for anomalies: customers with declining "
    "engagement, revenue drops, stalled pipelines, overdue invoices. Alert when "
    "thresholds are breached.\n\n"
    "Monitoring checks:\n"
    "- Customers with stage 'churned' or declining activity\n"
    "- Deals stuck in the same stage for too long\n"
    "- Invoices with status 'overdue'\n"
    "- Leads with status 'new' that have not been contacted\n"
    "- Revenue concentration risk (too much revenue from one customer)\n"
    "- Campaign spend vs budget mismatches\n\n"
    "For each anomaly found, report:\n"
    "1. What was detected\n"
    "2. Severity (critical / warning / info)\n"
    "3. Affected entities (with IDs and names)\n"
    "4. Recommended action"
)

REPORTER_SYSTEM_PROMPT = (
    "You are an Intelligence Reporter. Generate structured reports with sections, "
    "metrics, and recommendations. Use ontology data for every data point.\n\n"
    "Report format:\n"
    "1. Executive Summary (2-3 sentences)\n"
    "2. Key Metrics (tables with numbers)\n"
    "3. Highlights (what's going well)\n"
    "4. Concerns (what needs attention)\n"
    "5. Recommendations (actionable next steps)\n\n"
    "Always query the ontology to get real data. Never fabricate numbers. "
    "Use ontology_aggregate for summary stats and ontology_query_objects for "
    "specific examples. Include both the big picture and supporting details."
)


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

INTELLIGENCE_AGENTS: list[dict[str, Any]] = [
    {
        "id": "intel-analyst",
        "name": "Intelligence Analyst",
        "tier": AgentTier.EXECUTIVE,
        "model": "gpt-4o",
        "department": "intelligence",
        "description": (
            "Answers ad-hoc business questions by querying the ontology. "
            "Can traverse relationships, aggregate data, spot anomalies, "
            "and generate reports."
        ),
        "system_prompt": ANALYST_SYSTEM_PROMPT,
        "tools": [
            "ontology_query_objects",
            "ontology_get_neighbors",
            "ontology_aggregate",
            "ontology_search",
            "ontology_get_schema",
            "admin_query_metrics",
            "admin_search_knowledge",
        ],
    },
    {
        "id": "intel-monitor",
        "name": "Intelligence Monitor",
        "tier": AgentTier.WORKER,
        "model": "gpt-4o-mini",
        "department": "intelligence",
        "description": (
            "Continuous monitoring agent. Watches ontology for anomalies: "
            "customer churn signals, revenue drops, pipeline stalls, "
            "unusual patterns. Alerts via event bus."
        ),
        "system_prompt": MONITOR_SYSTEM_PROMPT,
        "tools": [
            "ontology_query_objects",
            "ontology_aggregate",
            "ontology_get_neighbors",
            "admin_query_metrics",
            "admin_query_events",
        ],
    },
    {
        "id": "intel-reporter",
        "name": "Intelligence Reporter",
        "tier": AgentTier.WORKER,
        "model": "gpt-4o",
        "department": "intelligence",
        "description": (
            "Generates structured reports: weekly pipeline review, "
            "customer health scorecard, campaign performance analysis. "
            "Outputs markdown or HTML."
        ),
        "system_prompt": REPORTER_SYSTEM_PROMPT,
        "tools": [
            "ontology_query_objects",
            "ontology_aggregate",
            "ontology_get_neighbors",
            "ontology_search",
            "admin_query_metrics",
        ],
    },
]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_intelligence_agents(
    registry: AgentRegistry,
    ontology_tools: Any = None,
) -> list[AgentConfig]:
    """Register intelligence agents in the existing agent registry.

    Parameters
    ----------
    registry : AgentRegistry
        The agent registry to add agents to.
    ontology_tools : OntologyTools, optional
        The ontology tools instance. If provided, the ontology tool names
        are included in each agent's allowed_tools list. This parameter is
        accepted for forward-compatibility but tool routing is handled by
        ToolExecutor, so the agents work as long as tools are registered
        there.

    Returns
    -------
    list[AgentConfig]
        The list of AgentConfig objects that were registered.
    """
    registered: list[AgentConfig] = []

    for agent_def in INTELLIGENCE_AGENTS:
        config = AgentConfig(
            agent_id=agent_def["id"],
            name=agent_def["name"],
            department=agent_def["department"],
            tier=agent_def["tier"],
            system_prompt=agent_def["system_prompt"],
            allowed_tools=list(agent_def["tools"]),
            model=agent_def["model"],
        )
        registry.register(config)
        registered.append(config)

    logger.info(
        "Registered %d intelligence agents: %s",
        len(registered),
        [a.agent_id for a in registered],
    )
    return registered
