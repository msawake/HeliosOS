"""
DealForge AI workflow templates.

5 pre-built workflow definitions for DealForge's classifieds aggregation operations.
"""

from __future__ import annotations

from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    WorkflowDefinition,
)


def create_deal_search_workflow(
    user_id: str,
    search_query: str,
    category: str,
    max_price: float,
    location: str,
    radius_miles: int = 25,
) -> WorkflowDefinition:
    """Deal search workflow — crawl, match, analyze, and alert for a user search."""
    builder = TaskGraphBuilder(f"Deal Search: {search_query} for {user_id}", "operational")

    builder.task(
        name="crawl_craigslist",
        agent="crawler-craigslist",
        description=f"Crawl Craigslist for '{search_query}' in {location} within {radius_miles} miles. Category: {category}. Max price: ${max_price}.",
        budget_tokens=15_000,
    ).task(
        name="crawl_fbmp",
        agent="crawler-fbmp",
        description=f"Crawl Facebook Marketplace for '{search_query}' in {location} within {radius_miles} miles. Category: {category}. Max price: ${max_price}.",
        budget_tokens=15_000,
    ).task(
        name="crawl_offerup",
        agent="crawler-offerup",
        description=f"Crawl OfferUp for '{search_query}' in {location} within {radius_miles} miles. Category: {category}. Max price: ${max_price}.",
        budget_tokens=15_000,
    ).task(
        name="crawl_ebay",
        agent="crawler-ebay",
        description=f"Crawl eBay for '{search_query}'. Category: {category}. Max price: ${max_price}. Focus on Buy It Now and auctions ending within 24h.",
        budget_tokens=15_000,
    ).task(
        name="fraud_check",
        agent="fraud-detector",
        description=f"Screen all listings from crawlers for fraud patterns. Flag suspicious listings for '{search_query}' in {category}.",
        budget_tokens=20_000,
        blocked_by=["crawl_craigslist", "crawl_fbmp", "crawl_offerup", "crawl_ebay"],
    ).task(
        name="price_analysis",
        agent="price-analyzer",
        description=f"Analyze pricing for all valid listings matching '{search_query}'. Calculate fair market value and deal ratings (hot/good/fair/overpriced).",
        budget_tokens=25_000,
        blocked_by=["fraud_check"],
    ).task(
        name="match_and_rank",
        agent="matcher-agent",
        description=f"Match and rank listings for user {user_id} searching '{search_query}'. Apply user preferences: category={category}, max_price=${max_price}, location={location}, radius={radius_miles}mi.",
        budget_tokens=20_000,
        blocked_by=["price_analysis"],
    ).task(
        name="send_alerts",
        agent="alert-agent",
        description=f"Send deal alerts to user {user_id} for top matches on '{search_query}'. Include deal ratings and price analysis. Respect user notification preferences.",
        budget_tokens=10_000,
        blocked_by=["match_and_rank"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "search_query": search_query,
        "category": category,
        "max_price": max_price,
        "location": location,
    }
    return workflow


def create_deal_negotiation_workflow(
    user_id: str,
    listing_id: str,
    listing_title: str,
    asking_price: float,
    target_price: float,
    platform: str,
) -> WorkflowDefinition:
    """Deal negotiation workflow — analyze, draft offer, and assist with negotiation."""
    builder = TaskGraphBuilder(f"Negotiation: {listing_title} for {user_id}", "operational")

    builder.task(
        name="market_analysis",
        agent="price-analyzer",
        description=f"Deep market analysis for '{listing_title}' (listing {listing_id} on {platform}). Asking price: ${asking_price}. Find comparable sales, price history, and fair market value.",
        budget_tokens=30_000,
    ).task(
        name="draft_offer",
        agent="negotiator-agent",
        description=f"Draft initial offer message for '{listing_title}' on {platform}. Asking: ${asking_price}, target: ${target_price}. Base offer on market analysis. Draft for user {user_id} review.",
        budget_tokens=25_000,
        blocked_by=["market_analysis"],
    ).task(
        name="fraud_verify",
        agent="fraud-detector",
        description=f"Verify listing {listing_id} ('{listing_title}') on {platform} for fraud signals before user engages. Check seller history, listing anomalies, and payment safety.",
        budget_tokens=20_000,
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "listing_id": listing_id,
        "listing_title": listing_title,
        "asking_price": asking_price,
        "target_price": target_price,
        "platform": platform,
    }
    return workflow


def create_fraud_check_workflow(
    listing_id: str,
    listing_title: str,
    platform: str,
    reported_by: str = "system",
) -> WorkflowDefinition:
    """Fraud investigation workflow for a flagged listing."""
    builder = TaskGraphBuilder(f"Fraud Check: {listing_title} ({platform})", "operational")

    builder.task(
        name="deep_analysis",
        agent="fraud-detector",
        description=f"Deep fraud analysis of listing {listing_id} ('{listing_title}') on {platform}. Reported by: {reported_by}. Check images, pricing, seller history, cross-platform duplicates.",
        budget_tokens=30_000,
    ).task(
        name="cross_reference",
        agent="price-analyzer",
        description=f"Cross-reference pricing of listing {listing_id} ('{listing_title}') against known market values. Flag if pricing is anomalous.",
        budget_tokens=20_000,
    ).task(
        name="support_review",
        agent="support-lead",
        description=f"Review fraud assessment for listing {listing_id} ('{listing_title}') on {platform}. Decide whether to flag, suppress, or report to marketplace.",
        budget_tokens=15_000,
        blocked_by=["deep_analysis", "cross_reference"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "listing_id": listing_id,
        "listing_title": listing_title,
        "platform": platform,
        "reported_by": reported_by,
    }
    return workflow


def create_user_onboarding_workflow(
    user_id: str,
    user_email: str,
    plan: str = "free",
    interests: list[str] | None = None,
) -> WorkflowDefinition:
    """User onboarding workflow — set up preferences, initial searches, and first alerts."""
    interest_list = interests or ["general"]
    builder = TaskGraphBuilder(f"Onboarding: {user_id}", "operational")

    builder.task(
        name="setup_profile",
        agent="support-agent",
        description=f"Set up user profile for {user_id} ({user_email}). Plan: {plan}. Configure notification preferences, location, and interests: {', '.join(interest_list)}.",
        budget_tokens=15_000,
    ).task(
        name="initial_search",
        agent="matcher-agent",
        description=f"Run initial deal matches for new user {user_id} based on interests: {', '.join(interest_list)}. Find top 10 deals across all marketplaces to show value immediately.",
        budget_tokens=20_000,
        blocked_by=["setup_profile"],
    ).task(
        name="welcome_email",
        agent="alert-agent",
        description=f"Send welcome email to {user_email} with top initial deals for interests: {', '.join(interest_list)}. Include tips for setting up saved searches.",
        budget_tokens=10_000,
        blocked_by=["initial_search"],
    ).task(
        name="billing_setup",
        agent="fin-billing",
        description=f"Set up billing for user {user_id} ({user_email}). Plan: {plan}. Configure Stripe subscription if paid plan.",
        budget_tokens=10_000,
        blocked_by=["setup_profile"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "user_email": user_email,
        "plan": plan,
        "interests": interest_list,
    }
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    target_categories: list[str],
) -> WorkflowDefinition:
    """Marketing campaign workflow for DealForge user acquisition."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}. Goal: {campaign_goal}. Budget: ${budget_usd}. Target deal categories: {', '.join(target_categories)}.",
        budget_tokens=40_000,
    ).task(
        name="content",
        agent="mkt-content",
        description=f"Create content assets for campaign: {campaign_name}. Category-specific landing pages, deal guides, and social content for: {', '.join(target_categories)}.",
        budget_tokens=60_000,
        blocked_by=["strategy"],
    ).task(
        name="ad_setup",
        agent="mkt-ppc",
        description=f"Set up Google Ads for campaign: {campaign_name}. Target category search terms for: {', '.join(target_categories)}. Budget: ${budget_usd}.",
        budget_tokens=40_000,
        blocked_by=["strategy"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels. Coordinate content, ads, and tracking.",
        budget_tokens=15_000,
        blocked_by=["content", "ad_setup"],
    ).task(
        name="analytics",
        agent="mkt-analytics",
        description=f"Track and analyze campaign: {campaign_name}. Monitor CAC, conversion rate, and subscriber growth against goal: {campaign_goal}.",
        budget_tokens=25_000,
        blocked_by=["launch"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "campaign_name": campaign_name,
        "budget_usd": budget_usd,
        "target_categories": target_categories,
    }
    return workflow
