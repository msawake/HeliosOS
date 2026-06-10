"""LeadForge knowledge base — seed data."""

from __future__ import annotations


def seed_knowledge_base(kb):
    """Seed the company knowledge base with LeadForge-specific policies."""

    kb.add(
        category="policy",
        title="Lead Scoring Rules",
        content=(
            "Leads are scored 0-100 based on engagement signals. "
            "Scores >= 75 qualify as hot leads for AE outreach. "
            "Factors: email opens (+5), page views (+3), demo requests (+20), "
            "pricing page views (+10), company size multiplier (enterprise x2)."
        ),
        tags=["lead scoring", "sales", "qualification"],
        created_by="system",
        department="sales",
    )

    kb.add(
        category="policy",
        title="Discount Approval Policy",
        content=(
            "Discounts up to 10%: AE approval. "
            "10-20%: Sales director approval. "
            "Over 20%: VP/CFO approval required."
        ),
        tags=["discount", "approval", "financial"],
        created_by="system",
        department="sales",
    )

    kb.add(
        category="policy",
        title="SOC1 Audit Requirements",
        content=(
            "All agents must log tool calls, budget changes, and approvals. "
            "Audit logs retained for 7 years. Immutability enforced via hash chaining."
        ),
        tags=["audit", "soc1", "compliance"],
        created_by="system",
        department="security",
    )
