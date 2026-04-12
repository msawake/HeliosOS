"""
Practical workflow definitions — 4 multi-agent DAGs.

Each workflow uses real single-agent tools as steps.
"""

from __future__ import annotations


def create_client_onboarding(engine, params: dict):
    """Client onboarding: CRM → research → emails → HITL → billing → kickoff."""
    client_name = params.get("client_name", "New Client")
    wf = engine.create_workflow(
        name=f"Onboard: {client_name}",
        description=f"End-to-end onboarding for {client_name}",
    )

    crm = engine.add_task(wf, "crm-setup", "call-to-crm",
        f"Create CRM account for {client_name}. Set up required fields.")
    research = engine.add_task(wf, "research", "competitor-monitor",
        f"Research {client_name}: company size, industry, tech stack, key contacts.",
        deps=[crm])
    emails = engine.add_task(wf, "welcome-emails", "email-triage",
        f"Draft welcome email sequence for {client_name} based on research findings.",
        deps=[research])
    report = engine.add_task(wf, "kickoff-prep", "client-reporter",
        f"Prepare kickoff meeting brief for {client_name}.",
        deps=[research])

    return wf


def create_weekly_review(engine, params: dict):
    """Weekly business review: 4 parallel data pulls → synthesis."""
    wf = engine.create_workflow(
        name="Weekly Business Review",
        description="Monday morning executive brief from all departments",
    )

    # Parallel data gathering
    finance = engine.add_task(wf, "finance-pull", "invoice-categorizer",
        "Summarize this week's financials: revenue, costs, margins, anomalies.")
    sales = engine.add_task(wf, "sales-pull", "call-to-crm",
        "Summarize pipeline: deals progressed, deals closed, deals lost, new leads.")
    marketing = engine.add_task(wf, "marketing-pull", "competitor-monitor",
        "Summarize marketing: competitor changes, campaign performance.")
    support = engine.add_task(wf, "support-pull", "ticket-router",
        "Summarize support: ticket volume, resolution time, top issues, CSAT.")

    # Synthesis
    report = engine.add_task(wf, "synthesize", "client-reporter",
        "Combine all department data into a 2-page executive brief. "
        "Include: summary, key metrics table, wins, issues, next week plan.",
        deps=[finance, sales, marketing, support])

    return wf


def create_incident_response(engine, params: dict):
    """Incident response: detect → analyze → draft comms → HITL → publish."""
    incident_type = params.get("incident_type", "unknown")
    description = params.get("description", "Anomaly detected")
    wf = engine.create_workflow(
        name=f"Incident: {incident_type}",
        description=description,
    )

    analyze = engine.add_task(wf, "analyze-tickets", "ticket-router",
        f"Analyze recent support tickets related to: {description}. "
        "Identify pattern, affected users, severity.")
    digest = engine.add_task(wf, "team-digest", "standup-digest",
        "Check team channel for any related updates or known issues.",
        deps=[analyze])
    comms = engine.add_task(wf, "draft-comms", "email-triage",
        f"Draft customer-facing status update for: {description}. "
        "Include: what happened, impact, what we're doing, ETA.",
        deps=[analyze, digest])

    return wf


def create_proposal_generation(engine, params: dict):
    """Proposal generation: research → pricing → write → legal → assemble."""
    company = params.get("company_name", "Target Company")
    wf = engine.create_workflow(
        name=f"Proposal: {company}",
        description=f"Generate proposal for {company}",
    )

    research = engine.add_task(wf, "research", "competitor-monitor",
        f"Research {company}: size, industry, tech stack, pain points, budget signals.")
    pricing = engine.add_task(wf, "pricing", "invoice-categorizer",
        f"Calculate custom pricing for {company} based on research. "
        "Use standard pricing tiers from knowledge base.",
        deps=[research])
    write = engine.add_task(wf, "write-sections", "client-reporter",
        f"Write proposal for {company}: executive summary, solution overview, "
        "timeline, case studies, team. Use research and pricing data.",
        deps=[research, pricing])
    legal = engine.add_task(wf, "legal-check", "contract-checker",
        "Review proposal terms. Flag any non-standard terms that need legal approval.",
        deps=[write])

    return wf


# Workflow registry
WORKFLOW_TEMPLATES = {
    "client-onboarding": create_client_onboarding,
    "weekly-review": create_weekly_review,
    "incident-response": create_incident_response,
    "proposal-generation": create_proposal_generation,
}
