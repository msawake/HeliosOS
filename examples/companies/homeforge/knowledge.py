"""
HomeForge AI knowledge base seed data.

Policies and procedures for the AI-powered real estate buyer's agent platform.
"""

from __future__ import annotations


def seed_knowledge_base(knowledge_base) -> None:
    """Seed the knowledge base with HomeForge AI policies and procedures."""
    knowledge_base.add(
        "policy", "Transaction Service Tiers",
        "Starter ($2,000): Search assistance, comp analysis, offer drafting. "
        "Full Service ($3,500): Everything plus negotiation, inspection coordination. "
        "Premium ($5,000): Everything plus closing coordination, post-close support. "
        "Retainer: $500 deposit credited toward transaction fee. "
        "Retainer refundable if no offer accepted within 6 months. "
        "No hidden fees — total cost disclosed upfront. "
        "Traditional agent commission (3%) on $400K home = $12,000. HomeForge saves $7-10K.",
        ["finance", "pricing"], "system"
    )
    knowledge_base.add(
        "policy", "Offer Submission Requirements",
        "All offers require explicit buyer approval before submission. "
        "Offer must include: purchase price, earnest money (1-3%), contingencies, "
        "closing timeline, inclusions/exclusions, and all state-required disclosures. "
        "Offers drafted by Opus-tier agent for maximum reasoning capability. "
        "Legal review required before submission. "
        "Counter-offers also require buyer approval. "
        "Never submit offer above buyer's stated budget without explicit confirmation.",
        ["transaction", "offers", "legal"], "system"
    )
    knowledge_base.add(
        "policy", "Fair Housing Act Compliance",
        "HomeForge must comply with the Fair Housing Act in ALL operations. "
        "Protected classes: race, color, national origin, religion, sex, "
        "familial status, disability. "
        "No steering: cannot direct buyers toward/away from neighborhoods based on demographics. "
        "No discriminatory marketing or ad targeting. "
        "Property descriptions must use objective criteria only. "
        "Neighborhood research must present factual data, never demographic characterizations. "
        "Violations carry severe penalties — zero tolerance.",
        ["legal", "compliance", "fair housing"], "system"
    )
    knowledge_base.add(
        "policy", "RESPA Compliance",
        "RESPA (Real Estate Settlement Procedures Act) governs all settlement services. "
        "No kickbacks or referral fees for mortgage, title, or insurance referrals. "
        "Good Faith Estimate of closing costs required. "
        "Affiliated business arrangements must be disclosed. "
        "Cannot require buyer to use specific service providers. "
        "Closing disclosure must be provided 3 business days before closing. "
        "HomeForge fee must be clearly disclosed as buyer's agent fee.",
        ["legal", "compliance", "respa"], "system"
    )
    knowledge_base.add(
        "policy", "MLS Data Rules",
        "MLS data must be displayed per IDX/RETS rules. "
        "Required attribution: MLS name, data freshness timestamp, disclaimer. "
        "Cannot modify listing data (price, description, photos). "
        "Must show accurate listing status (active, pending, sold). "
        "Data refresh: minimum every 5 minutes for active searches. "
        "Cannot scrape MLS — must use authorized data feeds. "
        "Listing agent contact info must be available.",
        ["search", "data", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "HITL Approval Requirements",
        "Offer submission: 2h SLA (buyer-approved offers to listing agent). "
        "Contract review: 24h SLA (purchase agreements, amendments). "
        "Earnest money: 4h SLA (deposit confirmations). "
        "Inspection decision: 12h SLA (repair requests, contingency decisions). "
        "Closing authorization: 24h SLA (final closing approval). "
        "Price reduction requests: 4h SLA (post-inspection negotiations). "
        "Content approval: 4h SLA (marketing materials).",
        ["operations", "hitl", "approvals"], "system"
    )
    knowledge_base.add(
        "policy", "Financial Approval Thresholds",
        "Up to $1,000: Department lead approval. "
        "$1,000-$5,000: CFO approval. "
        "$5,000-$10,000: CEO approval. "
        "Over $10,000: Human board approval. "
        "Ad spend increases >20%: CFO approval required. "
        "New market expansion: CEO and board approval. "
        "Mortgage lender partnerships: CEO approval (RESPA review required).",
        ["finance", "approval"], "system"
    )
    knowledge_base.add(
        "procedure", "Comp Analysis Methodology",
        "Minimum 3 comparable sales within 6 months and 1 mile radius. "
        "Adjust for: square footage ($50-150/sqft depending on market), "
        "lot size, condition (excellent/good/fair/poor), upgrades, "
        "garage, pool, view premium, and age of home. "
        "Weight more recent sales more heavily. "
        "Provide valuation range, not single point estimate. "
        "Confidence levels: High (5+ comps), Medium (3-4), Low (1-2). "
        "Flag unique properties where comps are limited.",
        ["search", "valuation", "methodology"], "system"
    )
    knowledge_base.add(
        "procedure", "Closing Process Checklist",
        "1. Offer accepted — open escrow, deposit earnest money (1-3 business days). "
        "2. Inspection period — schedule within 7-10 days typically. "
        "3. Appraisal — ordered by lender, usually within 2 weeks. "
        "4. Title search — verify clear title, order title insurance. "
        "5. Final loan approval — all conditions met. "
        "6. Closing disclosure — review 3 business days before closing. "
        "7. Final walkthrough — 24-48 hours before closing. "
        "8. Closing day — sign documents, wire funds, receive keys. "
        "Track all deadlines strictly — missed deadlines can void contracts.",
        ["transaction", "closing", "process"], "system"
    )
    knowledge_base.add(
        "policy", "Client Data Privacy",
        "Financial information stored encrypted at rest. "
        "No cross-client data sharing. "
        "Client can export all data (GDPR/CCPA right to portability). "
        "Client can delete account and data within 72 hours of request. "
        "Transaction records retained per state requirements (typically 3-5 years). "
        "Property search history retained while account active + 30 days.",
        ["privacy", "data", "compliance"], "system"
    )
