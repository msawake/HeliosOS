"""
InsureForge AI knowledge base seed data.

Policies and procedures for the AI-powered insurance comparison platform.
"""

from __future__ import annotations


def seed_knowledge_base(knowledge_base) -> None:
    """Seed the knowledge base with InsureForge AI policies and procedures."""
    knowledge_base.add(
        "policy", "Quote Generation Standards",
        "All quotes must be generated from carrier-approved APIs or rate tables. "
        "Minimum 5 carriers per quote request. "
        "Quote accuracy must match carrier's actual offered price within 2%. "
        "Quotes refresh every 15 minutes while user is active. "
        "All quotes show total annual premium, not just monthly. "
        "Quotes clearly labeled as estimates pending carrier underwriting.",
        ["quotes", "pricing", "accuracy"], "system"
    )
    knowledge_base.add(
        "policy", "Carrier Referral Fee Model",
        "Revenue comes from carrier referral fees, not user payments. "
        "Auto referral: $50-100 per bound policy. "
        "Home referral: $75-150 per bound policy. "
        "Life referral: $100-200 per bound policy. "
        "Health referral: $50-100 per bound policy. "
        "Renewal commissions: 25-50% of initial referral fee. "
        "Referral fees must never influence recommendations — always recommend "
        "best coverage for the user. Disclose referral relationship.",
        ["finance", "revenue", "referral"], "system"
    )
    knowledge_base.add(
        "policy", "State Insurance Regulations",
        "Insurance regulations are state-specific. "
        "Some states require comparison site licensing. "
        "NAIC guidelines apply nationally but enforcement is state-level. "
        "Required disclosures vary by state and insurance type. "
        "State minimum coverage requirements must be shown alongside recommendations. "
        "All marketing materials must comply with state advertising rules. "
        "FCRA compliance required for credit-based pricing states.",
        ["compliance", "legal", "regulation"], "system"
    )
    knowledge_base.add(
        "policy", "Data Privacy and Security",
        "HIPAA compliance required for all health insurance data. "
        "FCRA compliance for credit-based insurance scoring. "
        "SSN collected only at binding stage, never during quoting. "
        "All personal data encrypted at rest and in transit. "
        "No selling or sharing user data with third parties. "
        "User can delete all data within 72 hours of request. "
        "Data retention: active account + 30 days, except tax records.",
        ["privacy", "data", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "Recommendation Ethics",
        "Recommendations must prioritize adequate coverage over lowest price. "
        "Must clearly disclose InsureForge earns referral fees from carriers. "
        "Cannot guarantee claims outcomes or specific coverage decisions. "
        "Must explain recommendation reasoning to users. "
        "Flag when user's desired coverage is below recommended minimums. "
        "Never steer users toward carriers with higher referral fees. "
        "Comparison must be unbiased and based on coverage quality.",
        ["analysis", "ethics", "recommendations"], "system"
    )
    knowledge_base.add(
        "policy", "HITL Approval Requirements",
        "Policy binding: 4h SLA (user-initiated carrier applications). "
        "Claims escalation: 24h SLA (complex claims support issues). "
        "Regulatory review: 48h SLA (state compliance questions). "
        "Refund requests: 24h SLA (billing disputes). "
        "Carrier disputes: 48h SLA (referral fee disagreements). "
        "Content approval: 4h SLA (marketing materials). "
        "Account suspension: requires human approval always.",
        ["operations", "hitl", "approvals"], "system"
    )
    knowledge_base.add(
        "policy", "Financial Approval Thresholds",
        "Up to $1,000: Department lead approval. "
        "$1,000-$5,000: CFO approval. "
        "$5,000-$10,000: CEO approval. "
        "Over $10,000: Human board approval. "
        "Ad spend increases >20%: CFO approval required. "
        "New carrier agreements: CEO approval required. "
        "Carrier fee renegotiations: CEO approval required.",
        ["finance", "approval"], "system"
    )
    knowledge_base.add(
        "procedure", "Carrier Integration Standards",
        "New carrier integrations require: CEO approval, compliance review, "
        "state-by-state licensing verification, API sandbox testing, "
        "quote accuracy validation (within 2%), and gradual rollout. "
        "Carrier APIs must meet 99.5% uptime SLA. "
        "Rate table updates must be processed within 24 hours of carrier notification. "
        "Carrier data feeds must be validated against independent sources.",
        ["integration", "operations", "carriers"], "system"
    )
    knowledge_base.add(
        "procedure", "Claims Support Process",
        "InsureForge does not process claims — carriers handle all claims. "
        "Our role: help users understand coverage and navigate carrier process. "
        "Step 1: Review policy coverage for the claim type. "
        "Step 2: Provide carrier claims department contact information. "
        "Step 3: Guide user through required documentation. "
        "Step 4: Follow up on claim status if user requests. "
        "Escalate to support-lead if carrier is unresponsive.",
        ["support", "claims", "process"], "system"
    )
