"""
DealForge AI knowledge base seed data.

Policies and procedures for the classifieds aggregation platform.
"""

from __future__ import annotations


def seed_knowledge_base(knowledge_base) -> None:
    """Seed the knowledge base with DealForge AI policies and procedures."""
    knowledge_base.add(
        "policy", "Marketplace Crawling Rules",
        "Each marketplace has specific rate limits and ToS requirements: "
        "Craigslist: max 1 request per 3 seconds per IP, no CAPTCHA bypass. "
        "Facebook Marketplace: approved API only, no scraping. "
        "OfferUp: API rate limits per developer agreement. "
        "eBay: Browse API within approved quota. "
        "All crawlers must identify as DealForge user-agent. "
        "New marketplace integrations require CEO approval and legal review.",
        ["search", "crawling", "compliance"], "system"
    )
    knowledge_base.add(
        "procedure", "Deal Scoring Framework",
        "Deals are scored based on price vs. fair market value: "
        "Hot Deal: >30% below FMV (immediate alert). "
        "Good Deal: 15-30% below FMV (standard alert). "
        "Fair Price: within 15% of FMV (no alert unless user opts in). "
        "Overpriced: >15% above FMV (suppress from results). "
        "FMV calculated from minimum 3 comparable data points. "
        "Confidence levels: High (5+ comps), Medium (3-4 comps), Low (1-2 comps).",
        ["deals", "pricing", "scoring"], "system"
    )
    knowledge_base.add(
        "policy", "Fraud Detection Rules",
        "Flag listings matching these patterns: "
        "Price >50% below market with no explanation (condition, urgency). "
        "Stock photos or reverse-image matches to other listings. "
        "Requests to pay outside platform (gift cards, wire, Zelle to strangers). "
        "New accounts (<7 days) listing high-value items (>$500). "
        "Duplicate listings across platforms from different sellers. "
        "Shipping-only high-value items with no returns from new sellers. "
        "False positive rate target: <5%. All flags are advisory only.",
        ["deals", "fraud", "safety"], "system"
    )
    knowledge_base.add(
        "policy", "User Data Privacy",
        "User search preferences stored encrypted at rest. "
        "No cross-user data sharing or recommendation leaking. "
        "Saved search data retained while account active + 30 days after deletion. "
        "No selling or sharing user data with third parties. "
        "User can export all data (GDPR/CCPA right to portability). "
        "User can delete account and all data within 72 hours of request.",
        ["privacy", "data", "compliance"], "system"
    )
    knowledge_base.add(
        "policy", "Subscription & Billing",
        "Free: 5 saved searches, 3 alerts/day, basic categories. "
        "Basic ($5/mo): 20 saved searches, 10 alerts/day, all categories, price history. "
        "Pro ($10/mo): Unlimited searches, unlimited alerts, hot deal priority, negotiation assist. "
        "Premium ($15/mo): Everything in Pro + fraud protection, price predictions, multi-city. "
        "Annual plans: 2 months free. Failed payments: 3 retries over 7 days, then downgrade to Free. "
        "Refunds processed within 48 hours. No partial month refunds on annual plans.",
        ["finance", "billing", "pricing"], "system"
    )
    knowledge_base.add(
        "policy", "Alert Notification Rules",
        "Maximum 10 alerts per user per day (unless unlimited plan). "
        "Quiet hours: 10PM-7AM user local time (configurable). "
        "Hot deals can bypass quiet hours if user enables. "
        "Alert channels: push notification (default), email, SMS (Pro+ only). "
        "Alert-to-action conversion tracked for relevance tuning. "
        "Users can snooze alerts per search for 24h/7d/30d.",
        ["deals", "alerts", "notifications"], "system"
    )
    knowledge_base.add(
        "policy", "HITL Approval Requirements",
        "Purchase confirmation: 4h SLA (user-initiated transactions). "
        "Fraud review: 2h SLA (flagged listings requiring human verification). "
        "Dispute resolution: 24h SLA (user-reported issues with deals). "
        "Refund requests: 24h SLA (billing disputes). "
        "Content approval: 4h SLA (marketing content before publication). "
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
        "New vendor agreements: CEO approval required.",
        ["finance", "approval"], "system"
    )
