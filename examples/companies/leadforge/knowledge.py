"""
LeadForge AI knowledge base seed data.

Company policies, procedures, and decision frameworks specific to
the B2B lead generation business.
"""

from __future__ import annotations


def seed_knowledge_base(knowledge_base) -> None:
    """Seed the knowledge base with LeadForge AI policies and procedures."""
    knowledge_base.add(
        "procedure", "ICP Definition Framework",
        "Framework for defining Ideal Customer Profiles per client. Includes: industry verticals, "
        "company size (revenue and employee count), technology stack signals, buying triggers "
        "(hiring, funding, expansion), organizational maturity indicators, geographic targeting, "
        "decision-maker titles and roles. Every client engagement starts with ICP workshop.",
        ["sales", "icp", "targeting"], "system"
    )
    knowledge_base.add(
        "procedure", "Lead Scoring Criteria (BANT/MEDDIC)",
        "Lead scoring rubric: Budget (0-25 points: Has budget allocated or process identified?), "
        "Authority (0-25: Is contact a decision maker or has access?), Need (0-25: Expressed pain "
        "point matching solution?), Timeline (0-25: Active buying timeline within 90 days?). "
        "Score 70+: SQL (hand off to client). Score 40-69: MQL (enter nurture sequence). "
        "Score below 40: Archive (revisit quarterly). MEDDIC overlay for enterprise deals >$50K.",
        ["sales", "scoring", "qualification"], "system"
    )
    knowledge_base.add(
        "policy", "Outreach Compliance (CAN-SPAM / GDPR)",
        "CAN-SPAM: Must include physical address, unsubscribe link, honest subject lines, no "
        "misleading headers. Honor opt-outs within 10 business days. GDPR: Legitimate interest "
        "basis for B2B outreach, right to object must be honored within 72 hours, data processing "
        "records required, no outreach to personal email addresses in EU without consent. "
        "Maximum outreach frequency: 3 emails per prospect per week. Opt-out processed within 24h.",
        ["legal", "compliance", "outreach", "email"], "system"
    )
    knowledge_base.add(
        "procedure", "Email Outreach Cadence Rules",
        "Standard outreach sequence: Day 1: Intro email (personalized to prospect pain points). "
        "Day 3: LinkedIn connection request with custom note. Day 5: Follow-up email with value-add "
        "content (case study or whitepaper). Day 8: LinkedIn message. Day 12: Breakup email. "
        "Wait 30 days before re-engaging. Maximum 50 new prospects per SDR per day. All emails "
        "sent between 8am-6pm recipient local time. All sequences use client-approved templates.",
        ["sales", "outreach", "cadence"], "system"
    )
    knowledge_base.add(
        "procedure", "Qualification Criteria",
        "A lead qualifies as SQL when: (1) Confirmed budget or budget process identified, "
        "(2) Spoke with economic buyer or champion with access to buyer, (3) Expressed specific "
        "pain point our client's service addresses, (4) Timeline within 90 days, "
        "(5) No competing engagement with direct competitor. Minimum 3 of 5 criteria met. "
        "All SQLs must have a booked meeting or call scheduled with client sales team.",
        ["sales", "qualification", "sql"], "system"
    )
    knowledge_base.add(
        "policy", "Client SLA Framework",
        "Standard client SLAs by retainer tier: Starter ($3K/month): 50 qualified leads/month, "
        "5 SQLs, weekly email reporting. Growth ($5K/month): 100 qualified leads/month, "
        "10 SQLs, bi-weekly strategy calls, dedicated Slack channel. Enterprise ($10K/month): "
        "200 qualified leads/month, 20 SQLs, dedicated strategist, daily Slack channel, "
        "monthly QBR. Performance bonus: $500 per SQL that converts to opportunity. "
        "Meeting no-show rate must be below 15%.",
        ["operations", "client", "sla"], "system"
    )
    knowledge_base.add(
        "policy", "Financial Approval Thresholds",
        "Up to $1,000: Department lead approval. $1,000-$5,000: CFO approval. "
        "$5,000-$10,000: CEO approval. Over $10,000: Human board approval. "
        "Client refunds >$1,000 require CEO approval. Google Ads spend increases >20% "
        "require CFO approval. Performance bonus payouts auto-approved per SLA terms.",
        ["finance", "approval"], "system"
    )
    knowledge_base.add(
        "policy", "Escalation Protocol",
        "Level 1: Same-department — department lead arbitrates. "
        "Level 2: Cross-department — COO arbitrates (council pattern). "
        "Level 3: Strategic — escalate to human board with structured decision document. "
        "Level 4: Red line — ANY agent can bypass hierarchy for ethical/legal/safety concerns "
        "via ESCALATION_CRITICAL event. Client escalations: churn risk goes directly to "
        "sales-lead and exec-coo simultaneously.",
        ["operations", "escalation"], "system"
    )
    knowledge_base.add(
        "policy", "Data Handling Policy",
        "No PII in agent prompts or logs. Prospect data handled per client data processing "
        "agreements. No cross-client data sharing or list mixing under any circumstances. "
        "Data deletion follows GDPR workflow. All data access logged in audit trail. "
        "Prospect data retained for maximum 12 months after last engagement unless client "
        "requests extension. Suppression lists maintained per client and per jurisdiction.",
        ["legal", "data", "privacy"], "system"
    )
    knowledge_base.add(
        "policy", "Agent Autonomy Levels",
        "Category A (Fully Autonomous): Lead scoring, prospect research, CRM updates, "
        "template-based outreach, pipeline reporting, data enrichment. "
        "Category B (Autonomous + Audit): Outreach emails (10% weekly sample), nurture sequences, "
        "campaign optimization, ad bid adjustments, content creation. "
        "Category C (Pre-Approval Required): Client service agreements (48h SLA), ad spend "
        "changes >$500 (12h), new outreach channels (24h), pricing changes (24h). "
        "Category D (Human-Only): Legal agreements, regulatory filings, strategic pivots, "
        "data breach response, client terminations.",
        ["operations", "autonomy", "governance"], "system"
    )
