"""
TravelForge AI knowledge base seed data.

Policies and procedures for the AI-powered travel booking platform.
"""

from __future__ import annotations


def seed_knowledge_base(knowledge_base) -> None:
    """Seed the knowledge base with TravelForge AI policies and procedures."""
    knowledge_base.add(
        "policy", "Search & Pricing Display Rules",
        "All prices displayed must include total cost (fare + taxes + fees). "
        "DOT requires total price display for airfares. "
        "Resort fees and local taxes must be included in hotel totals. "
        "Car rental prices must include airport surcharges and mandatory fees. "
        "Price comparisons must compare identical or equivalent products. "
        "Never display a price that excludes mandatory charges.",
        ["search", "pricing", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "Booking Confirmation Requirements",
        "All bookings require explicit user confirmation before payment processing. "
        "Confirmation must show: total price, cancellation policy, change fees, "
        "and key terms. Booking confirmation email sent within 60 seconds. "
        "Booking reference must be stored and accessible in user account. "
        "PCI-DSS: never log, store, or display full card numbers. "
        "Payment processing exclusively through Stripe.",
        ["booking", "payment", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "Cancellation & Refund Policy",
        "Free cancellation: honor supplier's free cancellation window. "
        "Refunds processed within 48 hours of approval. "
        "Refunds up to $100: automatic per policy. "
        "Refunds $100-$500: support-lead approval required. "
        "Refunds >$500: fin-lead approval required. "
        "Non-refundable bookings: clearly flagged at booking time. "
        "Credit card refund timeline communicated to user (5-10 business days).",
        ["booking", "refund", "support"], "system"
    )
    knowledge_base.add(
        "policy", "Subscription & Billing",
        "Free: 5 searches/day, basic results, no price alerts. "
        "Explorer ($10/month): Unlimited searches, price alerts, price history. "
        "Traveler ($20/month): Everything plus priority booking, itinerary planner. "
        "Globe ($30/month): Everything plus price guarantee, multi-city, concierge. "
        "Per-booking fees for non-subscribers: flights $5, hotels $8, cars $5, packages $12. "
        "Annual plans: 2 months free. Failed payments: 3 retries over 7 days.",
        ["finance", "pricing"], "system"
    )
    knowledge_base.add(
        "policy", "Regulatory Compliance",
        "DOT: Total price display for airfares, no deceptive advertising. "
        "PCI-DSS: Secure payment handling, no card data storage. "
        "EU Package Travel Directive: Bundled packages have additional obligations "
        "(insolvency protection, pre-travel info, liability for performance). "
        "GDPR/CCPA: User data privacy, right to deletion, data portability. "
        "Consumer protection: Clear cancellation rights per jurisdiction.",
        ["compliance", "legal"], "system"
    )
    knowledge_base.add(
        "policy", "User Data Privacy",
        "Search history stored encrypted at rest. "
        "No cross-user data sharing or recommendation leaking. "
        "Travel preference data retained while account active + 30 days. "
        "No selling or sharing user data with third parties. "
        "User can export all data (GDPR/CCPA right to portability). "
        "User can delete account and all data within 72 hours of request. "
        "Booking data retained per regulatory requirements (tax records).",
        ["privacy", "data", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "HITL Approval Requirements",
        "Booking confirmation: 1h SLA (user-initiated bookings). "
        "Refund requests: 24h SLA. "
        "Dispute resolution: 24h SLA. "
        "Price guarantee claims: 4h SLA. "
        "Ad spend changes: 12h SLA. "
        "Content approval: 4h SLA. "
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
        "New supplier agreements: CEO approval required. "
        "Price guarantee payouts: automatic up to $50, support-lead above.",
        ["finance", "approval"], "system"
    )
    knowledge_base.add(
        "procedure", "Supplier API Integration Standards",
        "All supplier APIs must be tested in sandbox before production. "
        "Rate limits must be respected with exponential backoff. "
        "API responses cached for 15 minutes (availability) or 1 hour (pricing). "
        "Failed API calls trigger fallback to cached data with staleness indicator. "
        "New supplier integrations require: CEO approval, compliance review, "
        "sandbox testing, gradual rollout with canary monitoring.",
        ["search", "integration", "operations"], "system"
    )
