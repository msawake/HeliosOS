"""
Simulated MCP tool handlers for platform integrations.

Provides realistic tool handlers for external services (CRM, HTTP, ads,
MLS, insurance, GitHub, inter-agent messaging) that don't yet have real
MCP server backends. Each handler returns simulated but structurally
realistic data so agents can exercise full workflows end-to-end.

Registration pattern mirrors ToolExecutor._register_custom_tools():
handlers take (tool_input: dict, agent_context: dict | None) -> dict.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory mailbox store for inter-agent messaging
# ---------------------------------------------------------------------------

_agent_mailboxes: dict[str, list[dict]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# Handler functions
# ===========================================================================


# ── CRM ───────────────────────────────────────────────────────────────────


def handle_crm_search_leads(tool_input: dict, agent_context: dict | None) -> dict:
    """Search CRM for leads matching criteria. Returns simulated results."""
    query = tool_input.get("query", "")
    status = tool_input.get("status")
    min_score = tool_input.get("min_score", 0)
    limit = tool_input.get("limit", 10)

    # Simulated lead database
    leads = [
        {
            "lead_id": "lead-001",
            "name": "Demo Lead - Acme Corp",
            "company": "Acme Corp",
            "title": "VP of Engineering",
            "email": "jdoe@acme-demo.example.com",
            "score": 82,
            "status": "qualified",
            "source": "inbound_webinar",
            "owner": "sdr-agent-01",
            "last_activity": "2026-03-27T14:30:00Z",
            "deal_value": 15000,
        },
        {
            "lead_id": "lead-002",
            "name": "Demo Lead - Globex Inc",
            "company": "Globex Inc",
            "title": "CTO",
            "email": "msmith@globex-demo.example.com",
            "score": 74,
            "status": "qualified",
            "source": "outbound_cold",
            "owner": "sdr-agent-02",
            "last_activity": "2026-03-26T09:15:00Z",
            "deal_value": 8500,
        },
        {
            "lead_id": "lead-003",
            "name": "Demo Lead - Initech LLC",
            "company": "Initech LLC",
            "title": "Director of Operations",
            "email": "pbriggs@initech-demo.example.com",
            "score": 55,
            "status": "nurturing",
            "source": "content_download",
            "owner": "sdr-agent-01",
            "last_activity": "2026-03-25T16:45:00Z",
            "deal_value": 5000,
        },
        {
            "lead_id": "lead-004",
            "name": "Demo Lead - Umbrella Corp",
            "company": "Umbrella Corp",
            "title": "Head of Procurement",
            "email": "awong@umbrella-demo.example.com",
            "score": 91,
            "status": "sql",
            "source": "referral",
            "owner": "sdr-agent-03",
            "last_activity": "2026-03-27T11:00:00Z",
            "deal_value": 32000,
        },
        {
            "lead_id": "lead-005",
            "name": "Demo Lead - Wayne Enterprises",
            "company": "Wayne Enterprises",
            "title": "CFO",
            "email": "lf@wayne-demo.example.com",
            "score": 45,
            "status": "cold",
            "source": "tradeshow",
            "owner": "sdr-agent-02",
            "last_activity": "2026-03-20T08:00:00Z",
            "deal_value": 3000,
        },
    ]

    # Apply filters
    filtered = leads
    if query:
        q = query.lower()
        filtered = [l for l in filtered if q in l["name"].lower() or q in l["company"].lower()]
    if status:
        filtered = [l for l in filtered if l["status"] == status]
    if min_score:
        filtered = [l for l in filtered if l["score"] >= min_score]

    results = filtered[:limit]
    return {
        "simulated": True,
        "total_results": len(results),
        "leads": results,
        "query": query,
        "filters_applied": {"status": status, "min_score": min_score},
    }


def handle_crm_update_lead(tool_input: dict, agent_context: dict | None) -> dict:
    """Update a lead's status, score, or owner assignment."""
    lead_id = tool_input.get("lead_id", "lead-unknown")
    updates = {}
    for field in ("status", "score", "owner", "notes", "deal_value", "next_action"):
        if field in tool_input:
            updates[field] = tool_input[field]

    return {
        "simulated": True,
        "lead_id": lead_id,
        "updated_fields": updates,
        "updated_at": _now_iso(),
        "success": True,
    }


def handle_crm_get_pipeline(tool_input: dict, agent_context: dict | None) -> dict:
    """Get sales pipeline summary with stage breakdown."""
    owner = tool_input.get("owner")
    date_range = tool_input.get("date_range", "current_quarter")

    return {
        "simulated": True,
        "pipeline_summary": {
            "date_range": date_range,
            "owner_filter": owner,
            "total_value": 198500,
            "total_deals": 23,
            "weighted_value": 87340,
            "stages": [
                {"stage": "prospecting", "deals": 8, "value": 42000, "avg_age_days": 5},
                {"stage": "qualification", "deals": 6, "value": 51000, "avg_age_days": 12},
                {"stage": "proposal", "deals": 4, "value": 48000, "avg_age_days": 18},
                {"stage": "negotiation", "deals": 3, "value": 37500, "avg_age_days": 25},
                {"stage": "closed_won", "deals": 2, "value": 20000, "avg_age_days": 34},
            ],
            "conversion_rates": {
                "prospect_to_qualified": 0.62,
                "qualified_to_proposal": 0.55,
                "proposal_to_negotiation": 0.48,
                "negotiation_to_closed": 0.67,
                "overall": 0.11,
            },
            "avg_deal_size": 8630,
            "avg_cycle_days": 28,
        },
        "generated_at": _now_iso(),
    }


def handle_crm_create_activity(tool_input: dict, agent_context: dict | None) -> dict:
    """Log an activity (call, email, meeting, note) on a lead or deal."""
    activity_id = f"act-{uuid.uuid4().hex[:8]}"
    return {
        "simulated": True,
        "activity_id": activity_id,
        "lead_id": tool_input.get("lead_id", "lead-unknown"),
        "deal_id": tool_input.get("deal_id"),
        "activity_type": tool_input.get("activity_type", "note"),
        "subject": tool_input.get("subject", "Activity logged"),
        "body": tool_input.get("body", ""),
        "logged_by": (agent_context or {}).get("agent_id", "unknown"),
        "created_at": _now_iso(),
        "success": True,
    }


# ── HTTP ──────────────────────────────────────────────────────────────────


def handle_http_fetch(tool_input: dict, agent_context: dict | None) -> dict:
    """Fetch content from a URL (simulated scraping)."""
    url = tool_input.get("url", "")
    headers = tool_input.get("headers", {})
    selector = tool_input.get("selector")

    return {
        "simulated": True,
        "url": url,
        "status_code": 200,
        "content_type": "text/html",
        "content_length": 14823,
        "title": "Demo Page - Simulated Fetch Result",
        "text_content": (
            "This is simulated content fetched from the requested URL. "
            "In production, this would contain the actual page text extracted "
            f"from {url}. The content would be cleaned and formatted for agent consumption."
        ),
        "meta": {
            "description": "Simulated meta description for the fetched page.",
            "og_title": "Demo Page Title",
        },
        "selector_match": f"Content matching selector '{selector}'" if selector else None,
        "fetched_at": _now_iso(),
        "headers_sent": headers,
    }


def handle_http_post(tool_input: dict, agent_context: dict | None) -> dict:
    """POST data to an external API (simulated)."""
    url = tool_input.get("url", "")
    body = tool_input.get("body", {})
    headers = tool_input.get("headers", {})

    return {
        "simulated": True,
        "url": url,
        "method": "POST",
        "status_code": 200,
        "response": {
            "id": f"resp-{uuid.uuid4().hex[:8]}",
            "status": "accepted",
            "message": "Request processed successfully (simulated).",
        },
        "request_body_keys": list(body.keys()) if isinstance(body, dict) else [],
        "headers_sent": {k: v for k, v in headers.items() if k.lower() != "authorization"},
        "posted_at": _now_iso(),
    }


# ── Ads ───────────────────────────────────────────────────────────────────


def handle_ads_get_campaigns(tool_input: dict, agent_context: dict | None) -> dict:
    """Get ad campaign performance metrics."""
    platform = tool_input.get("platform", "google_ads")
    status_filter = tool_input.get("status", "active")
    date_range = tool_input.get("date_range", "last_7_days")

    campaigns = [
        {
            "campaign_id": "camp-gads-001",
            "name": "Demo Campaign - B2B SaaS Leads",
            "platform": platform,
            "status": "active",
            "budget_daily": 150.00,
            "spend_today": 87.42,
            "spend_period": 612.94,
            "impressions": 24580,
            "clicks": 347,
            "ctr": 0.0141,
            "cpc": 1.77,
            "conversions": 12,
            "cost_per_conversion": 51.08,
            "roas": 3.2,
        },
        {
            "campaign_id": "camp-gads-002",
            "name": "Demo Campaign - Retargeting Q1",
            "platform": platform,
            "status": "active",
            "budget_daily": 75.00,
            "spend_today": 52.18,
            "spend_period": 365.26,
            "impressions": 18340,
            "clicks": 512,
            "ctr": 0.0279,
            "cpc": 0.71,
            "conversions": 28,
            "cost_per_conversion": 13.05,
            "roas": 5.8,
        },
        {
            "campaign_id": "camp-gads-003",
            "name": "Demo Campaign - Brand Awareness",
            "platform": platform,
            "status": "paused",
            "budget_daily": 200.00,
            "spend_today": 0,
            "spend_period": 0,
            "impressions": 0,
            "clicks": 0,
            "ctr": 0,
            "cpc": 0,
            "conversions": 0,
            "cost_per_conversion": 0,
            "roas": 0,
        },
    ]

    if status_filter and status_filter != "all":
        campaigns = [c for c in campaigns if c["status"] == status_filter]

    return {
        "simulated": True,
        "platform": platform,
        "date_range": date_range,
        "campaigns": campaigns,
        "totals": {
            "total_spend": sum(c["spend_period"] for c in campaigns),
            "total_clicks": sum(c["clicks"] for c in campaigns),
            "total_conversions": sum(c["conversions"] for c in campaigns),
            "avg_cpc": round(
                sum(c["spend_period"] for c in campaigns) / max(sum(c["clicks"] for c in campaigns), 1), 2
            ),
        },
        "retrieved_at": _now_iso(),
    }


def handle_ads_update_bid(tool_input: dict, agent_context: dict | None) -> dict:
    """Update campaign bid strategy or budget."""
    campaign_id = tool_input.get("campaign_id", "camp-unknown")
    updates = {}
    for field in ("daily_budget", "bid_strategy", "target_cpa", "target_roas", "max_cpc", "status"):
        if field in tool_input:
            updates[field] = tool_input[field]

    return {
        "simulated": True,
        "campaign_id": campaign_id,
        "updated_fields": updates,
        "previous_values": {
            "daily_budget": 150.00,
            "bid_strategy": "maximize_conversions",
            "target_cpa": 50.00,
        },
        "requires_approval": updates.get("daily_budget", 0) > 500,
        "updated_at": _now_iso(),
        "success": True,
    }


# ── MLS / Real Estate ────────────────────────────────────────────────────


def handle_mls_search_listings(tool_input: dict, agent_context: dict | None) -> dict:
    """Search real estate listings by criteria."""
    city = tool_input.get("city", "Austin")
    min_price = tool_input.get("min_price", 0)
    max_price = tool_input.get("max_price", 999999999)
    bedrooms = tool_input.get("bedrooms")
    property_type = tool_input.get("property_type")
    limit = tool_input.get("limit", 10)

    listings = [
        {
            "mls_id": "MLS-2026-10042",
            "address": "1234 Demo Oak Lane",
            "city": city,
            "state": "TX",
            "zip": "78701",
            "price": 485000,
            "bedrooms": 3,
            "bathrooms": 2,
            "sqft": 1850,
            "lot_size_acres": 0.18,
            "property_type": "single_family",
            "year_built": 2018,
            "days_on_market": 12,
            "status": "active",
            "listing_agent": "Demo Agent - Sarah Chen",
            "photos_count": 24,
        },
        {
            "mls_id": "MLS-2026-10078",
            "address": "5678 Demo Elm Street, Unit 4B",
            "city": city,
            "state": "TX",
            "zip": "78704",
            "price": 325000,
            "bedrooms": 2,
            "bathrooms": 2,
            "sqft": 1200,
            "lot_size_acres": 0,
            "property_type": "condo",
            "year_built": 2021,
            "days_on_market": 5,
            "status": "active",
            "listing_agent": "Demo Agent - Mike Torres",
            "photos_count": 18,
        },
        {
            "mls_id": "MLS-2026-10115",
            "address": "910 Demo Cedar Blvd",
            "city": city,
            "state": "TX",
            "zip": "78745",
            "price": 725000,
            "bedrooms": 4,
            "bathrooms": 3.5,
            "sqft": 2800,
            "lot_size_acres": 0.35,
            "property_type": "single_family",
            "year_built": 2015,
            "days_on_market": 28,
            "status": "active",
            "listing_agent": "Demo Agent - Sarah Chen",
            "photos_count": 32,
        },
    ]

    # Apply price filters
    filtered = [l for l in listings if min_price <= l["price"] <= max_price]
    if bedrooms:
        filtered = [l for l in filtered if l["bedrooms"] >= bedrooms]
    if property_type:
        filtered = [l for l in filtered if l["property_type"] == property_type]

    return {
        "simulated": True,
        "total_results": len(filtered),
        "listings": filtered[:limit],
        "search_criteria": {
            "city": city,
            "min_price": min_price,
            "max_price": max_price,
            "bedrooms": bedrooms,
            "property_type": property_type,
        },
        "market_stats": {
            "median_price": 485000,
            "avg_days_on_market": 15,
            "active_listings_in_area": 142,
        },
    }


def handle_mls_get_listing(tool_input: dict, agent_context: dict | None) -> dict:
    """Get detailed information about a specific listing."""
    mls_id = tool_input.get("mls_id", "MLS-2026-10042")

    return {
        "simulated": True,
        "mls_id": mls_id,
        "address": "1234 Demo Oak Lane",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "county": "Travis",
        "price": 485000,
        "original_price": 499000,
        "price_per_sqft": 262.16,
        "bedrooms": 3,
        "bathrooms": 2,
        "sqft": 1850,
        "lot_size_acres": 0.18,
        "property_type": "single_family",
        "year_built": 2018,
        "construction": "Frame/Stucco",
        "roof": "Composition",
        "foundation": "Slab",
        "parking": "2-car attached garage",
        "heating": "Central",
        "cooling": "Central A/C",
        "hoa_fee_monthly": 0,
        "tax_annual": 8750,
        "days_on_market": 12,
        "status": "active",
        "listing_agent": "Demo Agent - Sarah Chen",
        "listing_office": "Demo Realty Group",
        "description": (
            "Beautifully maintained 3-bedroom home in a desirable neighborhood. "
            "Features an open floor plan, updated kitchen with quartz countertops, "
            "and a spacious backyard. Walking distance to parks and top-rated schools. "
            "(This is simulated listing data for demonstration purposes.)"
        ),
        "features": [
            "Open Floor Plan",
            "Updated Kitchen",
            "Quartz Countertops",
            "Stainless Appliances",
            "Hardwood Floors",
            "Fenced Backyard",
            "Covered Patio",
            "Sprinkler System",
        ],
        "school_district": "Austin ISD (Demo)",
        "photos_count": 24,
        "virtual_tour_url": "https://demo-tours.example.com/mls-2026-10042",
        "last_updated": "2026-03-27T10:00:00Z",
    }


# ── Insurance ─────────────────────────────────────────────────────────────


def handle_insurance_get_quotes(tool_input: dict, agent_context: dict | None) -> dict:
    """Get insurance quotes from simulated carriers."""
    insurance_type = tool_input.get("type", "auto")
    coverage_level = tool_input.get("coverage_level", "standard")
    state = tool_input.get("state", "TX")

    base_quotes = {
        "auto": [
            {"carrier": "Demo Carrier - SafeGuard Insurance", "carrier_id": "carrier-sg", "monthly_premium": 142.00, "annual_premium": 1704.00, "deductible": 500, "coverage_limit": 100000, "rating": "A+"},
            {"carrier": "Demo Carrier - Liberty Shield", "carrier_id": "carrier-ls", "monthly_premium": 128.50, "annual_premium": 1542.00, "deductible": 750, "coverage_limit": 100000, "rating": "A"},
            {"carrier": "Demo Carrier - National Secure", "carrier_id": "carrier-ns", "monthly_premium": 156.75, "annual_premium": 1881.00, "deductible": 250, "coverage_limit": 150000, "rating": "A++"},
        ],
        "home": [
            {"carrier": "Demo Carrier - HomeFirst Insurance", "carrier_id": "carrier-hf", "monthly_premium": 185.00, "annual_premium": 2220.00, "deductible": 1000, "coverage_limit": 350000, "rating": "A"},
            {"carrier": "Demo Carrier - SafeGuard Insurance", "carrier_id": "carrier-sg", "monthly_premium": 198.50, "annual_premium": 2382.00, "deductible": 500, "coverage_limit": 400000, "rating": "A+"},
            {"carrier": "Demo Carrier - National Secure", "carrier_id": "carrier-ns", "monthly_premium": 172.25, "annual_premium": 2067.00, "deductible": 1500, "coverage_limit": 300000, "rating": "A++"},
        ],
        "life": [
            {"carrier": "Demo Carrier - LifeSecure Mutual", "carrier_id": "carrier-lm", "monthly_premium": 45.00, "annual_premium": 540.00, "coverage_limit": 500000, "term_years": 20, "rating": "A+"},
            {"carrier": "Demo Carrier - National Secure", "carrier_id": "carrier-ns", "monthly_premium": 52.00, "annual_premium": 624.00, "coverage_limit": 500000, "term_years": 30, "rating": "A++"},
        ],
    }

    quotes = base_quotes.get(insurance_type, base_quotes["auto"])

    return {
        "simulated": True,
        "insurance_type": insurance_type,
        "coverage_level": coverage_level,
        "state": state,
        "quotes": quotes,
        "quote_valid_until": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "disclaimer": "These are simulated quotes for demonstration purposes only.",
    }


def handle_insurance_compare_rates(tool_input: dict, agent_context: dict | None) -> dict:
    """Compare insurance rates across multiple carriers."""
    insurance_type = tool_input.get("type", "auto")
    carrier_ids = tool_input.get("carrier_ids", [])
    coverage_amount = tool_input.get("coverage_amount", 100000)

    comparisons = [
        {
            "carrier": "Demo Carrier - SafeGuard Insurance",
            "carrier_id": "carrier-sg",
            "monthly_premium": 142.00,
            "annual_premium": 1704.00,
            "deductible_options": [250, 500, 750, 1000],
            "coverage_limit": coverage_amount,
            "financial_strength_rating": "A+",
            "customer_satisfaction": 4.2,
            "claims_response_days": 3,
            "discounts_available": ["multi-policy", "good-driver", "paperless"],
            "recommended": False,
        },
        {
            "carrier": "Demo Carrier - Liberty Shield",
            "carrier_id": "carrier-ls",
            "monthly_premium": 128.50,
            "annual_premium": 1542.00,
            "deductible_options": [500, 750, 1000],
            "coverage_limit": coverage_amount,
            "financial_strength_rating": "A",
            "customer_satisfaction": 4.0,
            "claims_response_days": 5,
            "discounts_available": ["multi-policy", "loyalty"],
            "recommended": True,
            "recommendation_reason": "Best value: lowest premium with strong financial rating.",
        },
        {
            "carrier": "Demo Carrier - National Secure",
            "carrier_id": "carrier-ns",
            "monthly_premium": 156.75,
            "annual_premium": 1881.00,
            "deductible_options": [250, 500, 750, 1000, 2500],
            "coverage_limit": coverage_amount,
            "financial_strength_rating": "A++",
            "customer_satisfaction": 4.6,
            "claims_response_days": 2,
            "discounts_available": ["multi-policy", "good-driver", "bundling", "paperless", "autopay"],
            "recommended": False,
        },
    ]

    if carrier_ids:
        comparisons = [c for c in comparisons if c["carrier_id"] in carrier_ids]

    return {
        "simulated": True,
        "insurance_type": insurance_type,
        "coverage_amount": coverage_amount,
        "comparisons": comparisons,
        "savings_potential": {
            "lowest_annual": min(c["annual_premium"] for c in comparisons) if comparisons else 0,
            "highest_annual": max(c["annual_premium"] for c in comparisons) if comparisons else 0,
            "max_annual_savings": (
                max(c["annual_premium"] for c in comparisons) - min(c["annual_premium"] for c in comparisons)
            ) if comparisons else 0,
        },
        "compared_at": _now_iso(),
    }


# ── GitHub ────────────────────────────────────────────────────────────────


def handle_github_get_pr(tool_input: dict, agent_context: dict | None) -> dict:
    """Get pull request details from GitHub (simulated)."""
    repo = tool_input.get("repo", "org/repo")
    pr_number = tool_input.get("pr_number", 42)

    return {
        "simulated": True,
        "repo": repo,
        "pr_number": pr_number,
        "title": "Demo PR - Add rate limiting to API endpoints",
        "state": "open",
        "author": "demo-developer",
        "branch": "feature/rate-limiting",
        "base_branch": "main",
        "created_at": "2026-03-26T10:30:00Z",
        "updated_at": "2026-03-27T15:00:00Z",
        "description": (
            "Adds rate limiting middleware to all public API endpoints. "
            "Uses Redis-backed sliding window algorithm. Configurable per-endpoint "
            "limits via settings. (Simulated PR data.)"
        ),
        "files_changed": 8,
        "additions": 342,
        "deletions": 18,
        "commits": 3,
        "labels": ["enhancement", "backend"],
        "reviewers": ["demo-reviewer-1", "demo-reviewer-2"],
        "checks": {
            "ci/tests": "passing",
            "ci/lint": "passing",
            "ci/type-check": "passing",
            "security/scan": "passing",
        },
        "mergeable": True,
        "review_status": "changes_requested",
        "comments_count": 4,
    }


def handle_github_create_review(tool_input: dict, agent_context: dict | None) -> dict:
    """Create a review comment on a pull request (simulated)."""
    repo = tool_input.get("repo", "org/repo")
    pr_number = tool_input.get("pr_number", 42)
    body = tool_input.get("body", "")
    event = tool_input.get("event", "COMMENT")  # APPROVE, REQUEST_CHANGES, COMMENT
    comments = tool_input.get("comments", [])

    review_id = f"review-{uuid.uuid4().hex[:8]}"
    return {
        "simulated": True,
        "review_id": review_id,
        "repo": repo,
        "pr_number": pr_number,
        "event": event,
        "body": body,
        "inline_comments_count": len(comments),
        "submitted_by": (agent_context or {}).get("agent_id", "unknown"),
        "submitted_at": _now_iso(),
        "success": True,
    }


# ── Inter-Agent Messaging ────────────────────────────────────────────────


def handle_send_message(tool_input: dict, agent_context: dict | None) -> dict:
    """Send a message to another agent's mailbox.

    Bridges to EventBus.publish when a company_system is available via
    agent_context, otherwise uses the in-memory mailbox.
    """
    recipient = tool_input.get("recipient", "unknown")
    subject = tool_input.get("subject", "")
    body = tool_input.get("body", "")
    priority = tool_input.get("priority", "normal")
    metadata = tool_input.get("metadata", {})

    sender = (agent_context or {}).get("agent_id", "unknown")
    message_id = f"msg-{uuid.uuid4().hex[:8]}"

    message = {
        "message_id": message_id,
        "from": sender,
        "to": recipient,
        "subject": subject,
        "body": body,
        "priority": priority,
        "metadata": metadata,
        "sent_at": _now_iso(),
        "read": False,
    }

    # Store in in-memory mailbox
    if recipient not in _agent_mailboxes:
        _agent_mailboxes[recipient] = []
    _agent_mailboxes[recipient].append(message)

    logger.info("Message %s sent from %s to %s: %s", message_id, sender, recipient, subject)

    return {
        "message_id": message_id,
        "delivered_to": recipient,
        "sent_at": message["sent_at"],
        "success": True,
    }


def handle_read_messages(tool_input: dict, agent_context: dict | None) -> dict:
    """Read messages from the agent's mailbox.

    Bridges to EventBus.query when a company_system is available via
    agent_context, otherwise reads from the in-memory mailbox.
    """
    agent_id = (agent_context or {}).get("agent_id", tool_input.get("agent_id", "unknown"))
    unread_only = tool_input.get("unread_only", False)
    limit = tool_input.get("limit", 20)
    mark_read = tool_input.get("mark_read", True)

    mailbox = _agent_mailboxes.get(agent_id, [])

    if unread_only:
        messages = [m for m in mailbox if not m["read"]]
    else:
        messages = list(mailbox)

    # Most recent first
    messages = sorted(messages, key=lambda m: m["sent_at"], reverse=True)[:limit]

    # Mark as read if requested
    if mark_read:
        for msg in messages:
            msg["read"] = True

    return {
        "agent_id": agent_id,
        "total_in_mailbox": len(mailbox),
        "unread_count": sum(1 for m in mailbox if not m["read"]),
        "returned_count": len(messages),
        "messages": messages,
    }


# ===========================================================================
# Tool Definitions (Anthropic tool format)
# ===========================================================================

PLATFORM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    # ── CRM ───────────────────────────────────────────────────────────
    {
        "name": "platform__crm_search_leads",
        "description": (
            "Search the CRM for leads by name, company, status, or score. "
            "Returns matching leads with contact info, scores, and deal values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search query (matches name or company)"},
                "status": {
                    "type": "string",
                    "enum": ["cold", "nurturing", "qualified", "sql", "opportunity", "closed_won", "closed_lost"],
                    "description": "Filter by lead status",
                },
                "min_score": {"type": "integer", "description": "Minimum lead score (0-100)"},
                "limit": {"type": "integer", "description": "Max results to return", "default": 10},
            },
        },
    },
    {
        "name": "platform__crm_update_lead",
        "description": (
            "Update a lead's status, score, owner assignment, deal value, "
            "or add notes. Use after qualifying or re-scoring a lead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "The lead ID to update"},
                "status": {
                    "type": "string",
                    "enum": ["cold", "nurturing", "qualified", "sql", "opportunity", "closed_won", "closed_lost"],
                },
                "score": {"type": "integer", "description": "New lead score (0-100)"},
                "owner": {"type": "string", "description": "Agent or user ID to assign as lead owner"},
                "notes": {"type": "string", "description": "Notes to append to the lead record"},
                "deal_value": {"type": "number", "description": "Updated estimated deal value in USD"},
                "next_action": {"type": "string", "description": "Next action to take on this lead"},
            },
            "required": ["lead_id"],
        },
    },
    {
        "name": "platform__crm_get_pipeline",
        "description": (
            "Get a summary of the sales pipeline including stage breakdown, "
            "conversion rates, average deal size, and cycle time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Filter pipeline by lead/deal owner"},
                "date_range": {
                    "type": "string",
                    "enum": ["today", "this_week", "this_month", "current_quarter", "this_year"],
                    "default": "current_quarter",
                },
            },
        },
    },
    {
        "name": "platform__crm_create_activity",
        "description": (
            "Log an activity (call, email, meeting, or note) on a lead or deal. "
            "Creates an auditable record of all touchpoints."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "Lead ID to log activity against"},
                "deal_id": {"type": "string", "description": "Deal ID (optional, for deal-level activities)"},
                "activity_type": {
                    "type": "string",
                    "enum": ["call", "email", "meeting", "note", "task", "demo"],
                    "description": "Type of activity",
                },
                "subject": {"type": "string", "description": "Short subject line for the activity"},
                "body": {"type": "string", "description": "Detailed activity notes or content"},
            },
            "required": ["lead_id", "activity_type", "subject"],
        },
    },
    # ── HTTP ──────────────────────────────────────────────────────────
    {
        "name": "platform__http_fetch",
        "description": (
            "Fetch content from a URL. Returns the page title, text content, "
            "and metadata. Useful for scraping prospect websites, checking "
            "competitor pages, or gathering public information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "headers": {"type": "object", "description": "Optional HTTP headers to send"},
                "selector": {"type": "string", "description": "Optional CSS selector to extract specific content"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "platform__http_post",
        "description": (
            "Send a POST request to an external API endpoint. "
            "Useful for webhook integrations, form submissions, or API calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to POST to"},
                "body": {"type": "object", "description": "JSON body to send"},
                "headers": {"type": "object", "description": "Optional HTTP headers"},
            },
            "required": ["url", "body"],
        },
    },
    # ── Ads ───────────────────────────────────────────────────────────
    {
        "name": "platform__ads_get_campaigns",
        "description": (
            "Get ad campaign performance metrics including spend, impressions, "
            "clicks, CTR, CPC, conversions, and ROAS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["google_ads", "meta_ads", "linkedin_ads", "twitter_ads"],
                    "default": "google_ads",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "all"],
                    "default": "active",
                },
                "date_range": {
                    "type": "string",
                    "enum": ["today", "last_7_days", "last_30_days", "this_month", "last_month"],
                    "default": "last_7_days",
                },
            },
        },
    },
    {
        "name": "platform__ads_update_bid",
        "description": (
            "Update a campaign's bid strategy, daily budget, target CPA, "
            "target ROAS, or max CPC. Changes above $500/day require approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "campaign_id": {"type": "string", "description": "Campaign ID to update"},
                "daily_budget": {"type": "number", "description": "New daily budget in USD"},
                "bid_strategy": {
                    "type": "string",
                    "enum": ["maximize_conversions", "target_cpa", "target_roas", "manual_cpc", "maximize_clicks"],
                },
                "target_cpa": {"type": "number", "description": "Target cost per acquisition in USD"},
                "target_roas": {"type": "number", "description": "Target return on ad spend (e.g., 3.0 = 300%)"},
                "max_cpc": {"type": "number", "description": "Maximum cost per click in USD"},
                "status": {"type": "string", "enum": ["active", "paused"]},
            },
            "required": ["campaign_id"],
        },
    },
    # ── MLS / Real Estate ─────────────────────────────────────────────
    {
        "name": "platform__mls_search_listings",
        "description": (
            "Search MLS real estate listings by location, price range, bedrooms, "
            "and property type. Returns listings with details and market stats."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search in"},
                "state": {"type": "string", "description": "State abbreviation (e.g., TX, CA)"},
                "min_price": {"type": "number", "description": "Minimum listing price in USD"},
                "max_price": {"type": "number", "description": "Maximum listing price in USD"},
                "bedrooms": {"type": "integer", "description": "Minimum number of bedrooms"},
                "property_type": {
                    "type": "string",
                    "enum": ["single_family", "condo", "townhouse", "multi_family", "land"],
                },
                "limit": {"type": "integer", "description": "Max results to return", "default": 10},
            },
            "required": ["city"],
        },
    },
    {
        "name": "platform__mls_get_listing",
        "description": (
            "Get full details for a specific MLS listing including property "
            "features, tax info, school district, and listing agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mls_id": {"type": "string", "description": "The MLS listing ID"},
            },
            "required": ["mls_id"],
        },
    },
    # ── Insurance ─────────────────────────────────────────────────────
    {
        "name": "platform__insurance_get_quotes",
        "description": (
            "Get insurance quotes from multiple carriers for a given type "
            "and coverage level. Returns premiums, deductibles, and ratings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["auto", "home", "life", "health", "business"],
                    "description": "Type of insurance",
                },
                "coverage_level": {
                    "type": "string",
                    "enum": ["basic", "standard", "premium"],
                    "default": "standard",
                },
                "state": {"type": "string", "description": "State for rate calculation (e.g., TX, CA)"},
                "details": {"type": "object", "description": "Additional details (age, vehicle info, property info, etc.)"},
            },
            "required": ["type"],
        },
    },
    {
        "name": "platform__insurance_compare_rates",
        "description": (
            "Compare insurance rates across carriers side-by-side. Shows premiums, "
            "deductible options, customer satisfaction, claims response time, "
            "and available discounts. Highlights the recommended option."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["auto", "home", "life", "health", "business"],
                    "description": "Type of insurance",
                },
                "carrier_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific carrier IDs to compare (omit for all available)",
                },
                "coverage_amount": {"type": "number", "description": "Desired coverage amount in USD"},
            },
            "required": ["type"],
        },
    },
    # ── GitHub ────────────────────────────────────────────────────────
    {
        "name": "platform__github_get_pr",
        "description": (
            "Get details of a GitHub pull request including title, description, "
            "files changed, CI check status, and review status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository in 'owner/repo' format"},
                "pr_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["repo", "pr_number"],
        },
    },
    {
        "name": "platform__github_create_review",
        "description": (
            "Create a review on a GitHub pull request. Can approve, "
            "request changes, or leave a comment with optional inline comments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository in 'owner/repo' format"},
                "pr_number": {"type": "integer", "description": "Pull request number"},
                "body": {"type": "string", "description": "Review comment body"},
                "event": {
                    "type": "string",
                    "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                    "default": "COMMENT",
                },
                "comments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path relative to repo root"},
                            "line": {"type": "integer", "description": "Line number to comment on"},
                            "body": {"type": "string", "description": "Inline comment body"},
                        },
                        "required": ["path", "line", "body"],
                    },
                    "description": "Inline review comments on specific lines",
                },
            },
            "required": ["repo", "pr_number", "body"],
        },
    },
    # ── Inter-Agent Messaging ─────────────────────────────────────────
    {
        "name": "platform__send_message",
        "description": (
            "Send a message to another agent. The message is delivered to the "
            "recipient's mailbox and can be read with platform__read_messages. "
            "Use for async inter-agent coordination."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Agent ID of the recipient"},
                "subject": {"type": "string", "description": "Message subject line"},
                "body": {"type": "string", "description": "Message body content"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "default": "normal",
                },
                "metadata": {"type": "object", "description": "Optional structured metadata to include"},
            },
            "required": ["recipient", "subject", "body"],
        },
    },
    {
        "name": "platform__read_messages",
        "description": (
            "Read messages from the agent's mailbox. Returns messages sorted "
            "by most recent first. Can filter to unread only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID whose mailbox to read (defaults to current agent)"},
                "unread_only": {"type": "boolean", "description": "Only return unread messages", "default": False},
                "limit": {"type": "integer", "description": "Max messages to return", "default": 20},
                "mark_read": {"type": "boolean", "description": "Mark returned messages as read", "default": True},
            },
        },
    },
]

# ===========================================================================
# Handler registry (maps tool name -> handler function)
# ===========================================================================

_HANDLER_MAP: dict[str, Any] = {
    "platform__crm_search_leads": handle_crm_search_leads,
    "platform__crm_update_lead": handle_crm_update_lead,
    "platform__crm_get_pipeline": handle_crm_get_pipeline,
    "platform__crm_create_activity": handle_crm_create_activity,
    "platform__http_fetch": handle_http_fetch,
    "platform__http_post": handle_http_post,
    "platform__ads_get_campaigns": handle_ads_get_campaigns,
    "platform__ads_update_bid": handle_ads_update_bid,
    "platform__mls_search_listings": handle_mls_search_listings,
    "platform__mls_get_listing": handle_mls_get_listing,
    "platform__insurance_get_quotes": handle_insurance_get_quotes,
    "platform__insurance_compare_rates": handle_insurance_compare_rates,
    "platform__github_get_pr": handle_github_get_pr,
    "platform__github_create_review": handle_github_create_review,
    "platform__send_message": handle_send_message,
    "platform__read_messages": handle_read_messages,
}


# ===========================================================================
# Registration helper
# ===========================================================================


def register_platform_tools(tool_executor) -> list[dict[str, Any]]:
    """Register all platform tool handlers with a ToolExecutor instance.

    Adds each handler to the executor's ``_custom_handlers`` dict so that
    ``ToolExecutor.execute()`` can dispatch ``platform__*`` calls through
    the same code path as ``company__*`` custom tools.

    Returns the full list of tool definitions (``PLATFORM_TOOL_DEFINITIONS``)
    so callers can include them in the tool list sent to the Claude API.

    Usage::

        from src.mcp.platform_tools import register_platform_tools

        tool_defs = register_platform_tools(tool_executor)
        # tool_defs is the list of Anthropic-format tool schemas
    """
    for tool_name, handler_fn in _HANDLER_MAP.items():
        tool_executor._custom_handlers[tool_name] = handler_fn
        logger.debug("Registered platform tool: %s", tool_name)

    logger.info("Registered %d platform tools", len(_HANDLER_MAP))
    return list(PLATFORM_TOOL_DEFINITIONS)
