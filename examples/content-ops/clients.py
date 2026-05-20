"""
Client configurations for the Content Operations Pipeline.

Each client is a namespace with its own rules, budget, brand voice,
compliance requirements, and tool restrictions.
"""

CLIENTS = {
    "pharma-co": {
        "namespace": "client-pharma-co",
        "name": "PharmaCo Health",
        "domain": "healthcare",
        "regulated": True,
        "compliance": ["HIPAA", "FDA"],
        "budget_monthly_usd": 1500.0,
        "brand_voice": (
            "Professional, empathetic, evidence-based. "
            "Always cite clinical studies. Never make absolute health claims. "
            "Use 'may help' instead of 'cures'. Include FDA disclaimers on "
            "all product-related content. Tone: warm but authoritative."
        ),
        "content_rules": [
            "All health claims require clinical citation",
            "No absolute claims (cures, guarantees, prevents)",
            "FDA disclaimer required on product content",
            "No AI-generated medical images",
            "Patient testimonials need IRB approval reference",
        ],
        "tools_denied": ["image.generate"],
        "hitl_required": True,
        "hitl_reason": "Healthcare content requires medical review before publication",
        "sample_topics": [
            "5 Ways to Support Heart Health This Summer",
            "Understanding Your Lab Results: A Patient Guide",
            "New Research on Gut Microbiome and Immunity",
        ],
    },
    "fintech-xyz": {
        "namespace": "client-fintech-xyz",
        "name": "FinTech XYZ",
        "domain": "financial_services",
        "regulated": True,
        "compliance": ["SEC", "FINRA"],
        "budget_monthly_usd": 800.0,
        "brand_voice": (
            "Confident, clear, data-driven. Use numbers and percentages. "
            "Avoid jargon — explain complex concepts simply. "
            "Never make investment predictions or guarantees. "
            "Always include risk disclaimers on financial content."
        ),
        "content_rules": [
            "No investment advice or return predictions",
            "Risk disclaimer required on all financial content",
            "No specific stock/crypto recommendations",
            "Performance data must include time period and benchmark",
            "Forward-looking statements need safe harbor language",
        ],
        "tools_denied": [],
        "hitl_required": True,
        "hitl_reason": "Financial content requires compliance review (SEC/FINRA)",
        "sample_topics": [
            "How AI Is Transforming Personal Finance Management",
            "Understanding Digital Banking Security in 2026",
            "The Rise of Embedded Finance: What SMBs Need to Know",
        ],
    },
    "ecommerce-shop": {
        "namespace": "client-ecommerce",
        "name": "ShopWave",
        "domain": "retail",
        "regulated": False,
        "compliance": [],
        "budget_monthly_usd": 500.0,
        "brand_voice": (
            "Friendly, energetic, casual. Use emojis sparingly. "
            "Speak like a knowledgeable friend, not a salesperson. "
            "Short sentences, punchy headlines. "
            "Focus on lifestyle benefits, not features."
        ),
        "content_rules": [
            "CAN-SPAM compliant for email content",
            "No false scarcity ('only 2 left!' without real data)",
            "Price claims must be accurate at time of publication",
        ],
        "tools_denied": [],
        "hitl_required": False,
        "hitl_reason": "",
        "sample_topics": [
            "Summer Sale Preview: 10 Must-Have Items Under $50",
            "How to Style the New Linen Collection",
            "Customer Spotlight: Sarah's Home Office Makeover",
        ],
    },
}

CONTENT_TYPES = {
    "blog_post": {
        "word_count": "800-1200 words",
        "structure": "Title, intro, 3-5 sections with subheadings, conclusion, CTA",
    },
    "social_post": {
        "word_count": "50-150 words",
        "structure": "Hook, body, CTA, hashtags",
    },
    "email_campaign": {
        "word_count": "200-400 words",
        "structure": "Subject line, preview text, greeting, body, CTA button text, unsubscribe note",
    },
}
