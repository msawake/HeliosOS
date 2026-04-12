"""Seed knowledge for practical agents — templates, rules, standard terms."""

from __future__ import annotations


def seed_knowledge_base(knowledge):
    """Seed the knowledge base with practical agent data."""

    knowledge.add(
        category="policy",
        title="Email Classification Rules",
        content=(
            "IGNORE: newsletters, automated notifications, marketing blasts, "
            "no-reply senders, social media notifications.\n"
            "QUICK_REPLY: scheduling confirmations, simple yes/no questions, "
            "thank you responses, FYI forwards.\n"
            "NEEDS_ATTENTION: project updates requiring input, multi-part questions, "
            "requests from direct reports, anything mentioning budget or timeline.\n"
            "URGENT: messages from CEO/CFO/clients, anything with 'urgent'/'ASAP' "
            "in subject, contract deadlines, escalated support issues."
        ),
        tags=["email", "classification", "rules"],
        created_by="practical-seed",
    )

    knowledge.add(
        category="policy",
        title="Transaction Categories",
        content=(
            "SaaS_Software: Recurring subscriptions (AWS, Slack, Notion, etc.)\n"
            "Travel: Flights, hotels, car rentals, ride-sharing\n"
            "Office_Supplies: Physical supplies, furniture, equipment\n"
            "Contractor: Freelancer payments, agency fees\n"
            "Marketing_Ads: Google Ads, LinkedIn Ads, Facebook Ads, sponsorships\n"
            "Professional_Services: Legal, accounting, consulting\n"
            "Food_Entertainment: Team meals, client dinners, events\n"
            "Utilities: Internet, phone, electricity, rent\n"
            "Insurance: Business insurance, D&O, health\n"
            "Other: Anything not matching above categories"
        ),
        tags=["finance", "categories", "rules"],
        created_by="practical-seed",
    )

    knowledge.add(
        category="policy",
        title="Standard Contract Terms",
        content=(
            "ACCEPTABLE (GREEN):\n"
            "- Net 30 payment terms\n"
            "- Mutual NDA with 2-year term\n"
            "- Standard indemnification (each party indemnifies for own negligence)\n"
            "- Termination with 30-day notice\n"
            "- Liability cap at 12 months of fees\n\n"
            "NEGOTIATE (YELLOW):\n"
            "- Net 60+ payment terms\n"
            "- One-sided indemnification\n"
            "- Auto-renewal with <60 day cancellation window\n"
            "- Liability cap at <6 months of fees\n"
            "- Non-compete clauses\n\n"
            "REJECT/ESCALATE (RED):\n"
            "- IP assignment to vendor\n"
            "- Unlimited liability\n"
            "- No termination clause\n"
            "- Mandatory arbitration in foreign jurisdiction\n"
            "- Data handling without GDPR compliance"
        ),
        tags=["legal", "contract", "terms"],
        created_by="practical-seed",
    )

    knowledge.add(
        category="faq",
        title="Common Support Responses",
        content=(
            "PASSWORD_RESET: 'You can reset your password at [link]. "
            "If you don't receive the email within 5 minutes, check your spam folder.'\n\n"
            "BILLING_QUESTION: 'Your current plan details are available in Settings > Billing. "
            "For changes to your subscription, please contact billing@company.com.'\n\n"
            "BUG_REPORT: 'Thank you for reporting this. I've logged it with our engineering team "
            "and they'll investigate. We'll update you within 24 hours.'\n\n"
            "FEATURE_REQUEST: 'Thanks for the suggestion! I've added it to our feature request "
            "tracker. Our product team reviews these monthly.'"
        ),
        tags=["support", "faq", "templates"],
        created_by="practical-seed",
    )

    knowledge.add(
        category="policy",
        title="Pricing Guide",
        content=(
            "STARTER: $3,000/month — Up to 5 users, basic features\n"
            "GROWTH: $5,000/month — Up to 20 users, advanced features, priority support\n"
            "ENTERPRISE: $10,000+/month — Unlimited users, custom features, dedicated support\n\n"
            "DISCOUNTS:\n"
            "- Annual prepay: 15% discount\n"
            "- Startup (<50 employees): 20% discount\n"
            "- Non-profit: 30% discount\n"
            "- Discounts >15% require sales-lead approval\n"
            "- Custom pricing requires CFO approval"
        ),
        tags=["pricing", "sales", "guide"],
        created_by="practical-seed",
    )

    return 5  # number of entries seeded
