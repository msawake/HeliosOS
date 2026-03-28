"""
HomeForge AI workflow templates.

8 pre-built workflow definitions for HomeForge's real estate operations.
"""

from __future__ import annotations

from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    WorkflowDefinition,
)


def create_property_search_workflow(
    buyer_id: str,
    city: str,
    max_price: float,
    bedrooms: int,
    criteria: str = "",
) -> WorkflowDefinition:
    """Property search workflow — MLS search, comp analysis, neighborhood, scoring."""
    builder = TaskGraphBuilder(f"Property Search: {city} for {buyer_id}", "operational")

    builder.task(
        name="mls_search",
        agent="mls-search",
        description=f"Search MLS for properties in {city}. Max price: ${max_price:,.0f}. Bedrooms: {bedrooms}+. Additional criteria: {criteria or 'none'}.",
        budget_tokens=15_000,
    ).task(
        name="comp_analysis",
        agent="comp-analyzer",
        description=f"Run comp analysis on search results in {city}. Determine fair market value for each property. Flag over/under-priced listings.",
        budget_tokens=20_000,
        blocked_by=["mls_search"],
    ).task(
        name="neighborhood_research",
        agent="neighborhood-research",
        description=f"Research neighborhoods for search results in {city}. Schools, crime, walkability, commute data, and future development.",
        budget_tokens=20_000,
        blocked_by=["mls_search"],
    ).task(
        name="score_properties",
        agent="property-scorer",
        description=f"Score all properties for buyer {buyer_id} in {city}. Apply buyer preferences: {bedrooms}+ BR, max ${max_price:,.0f}. {criteria}",
        budget_tokens=15_000,
        blocked_by=["comp_analysis", "neighborhood_research"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "city": city,
        "max_price": max_price,
        "bedrooms": bedrooms,
    }
    return workflow


def create_showing_workflow(
    buyer_id: str,
    property_ids: list[str],
    preferred_date: str,
) -> WorkflowDefinition:
    """Showing workflow — schedule, route optimize, collect feedback."""
    builder = TaskGraphBuilder(f"Showings: {len(property_ids)} properties for {buyer_id}", "operational")
    props = ", ".join(property_ids[:5])

    builder.task(
        name="schedule_showings",
        agent="showing-scheduler",
        description=f"Schedule showings for buyer {buyer_id}. Properties: {props}. Preferred date: {preferred_date}. Coordinate with listing agents.",
        budget_tokens=15_000,
    ).task(
        name="route_optimize",
        agent="showing-scheduler",
        description=f"Optimize showing route for buyer {buyer_id} on {preferred_date}. Minimize driving between {len(property_ids)} properties.",
        budget_tokens=10_000,
        blocked_by=["schedule_showings"],
    ).task(
        name="prep_packets",
        agent="comp-analyzer",
        description=f"Prepare property information packets for buyer {buyer_id} showings. Include comp data, neighborhood info, and pricing analysis.",
        budget_tokens=15_000,
        blocked_by=["schedule_showings"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "property_ids": property_ids,
        "preferred_date": preferred_date,
    }
    return workflow


def create_offer_workflow(
    buyer_id: str,
    property_id: str,
    property_address: str,
    list_price: float,
    offer_price: float,
) -> WorkflowDefinition:
    """Offer workflow — comp analysis, draft offer, legal review, submit."""
    builder = TaskGraphBuilder(f"Offer: {property_address} for {buyer_id}", "operational")

    builder.task(
        name="deep_comp_analysis",
        agent="comp-analyzer",
        description=f"Deep comp analysis for {property_address} (MLS {property_id}). List price: ${list_price:,.0f}. Determine fair value range and offer strategy support.",
        budget_tokens=25_000,
    ).task(
        name="draft_offer",
        agent="offer-drafter",
        description=f"Draft purchase offer for {property_address}. List: ${list_price:,.0f}. Buyer's target: ${offer_price:,.0f}. Include contingencies, earnest money, and timeline. Buyer: {buyer_id}.",
        budget_tokens=30_000,
        blocked_by=["deep_comp_analysis"],
    ).task(
        name="legal_review",
        agent="compliance-agent",
        description=f"Legal review of offer for {property_address}. Verify state compliance, required disclosures, and contract terms.",
        budget_tokens=15_000,
        blocked_by=["draft_offer"],
    ).task(
        name="submit_offer",
        agent="tx-lead",
        description=f"Submit reviewed offer for {property_address} to listing agent. Buyer {buyer_id} must confirm before submission. Offer: ${offer_price:,.0f}.",
        budget_tokens=10_000,
        blocked_by=["legal_review"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "property_id": property_id,
        "property_address": property_address,
        "list_price": list_price,
        "offer_price": offer_price,
    }
    return workflow


def create_negotiation_workflow(
    buyer_id: str,
    property_address: str,
    our_offer: float,
    counter_price: float,
    seller_terms: str,
) -> WorkflowDefinition:
    """Negotiation workflow — analyze counter, recommend response, draft reply."""
    builder = TaskGraphBuilder(f"Negotiation: {property_address} for {buyer_id}", "operational")

    builder.task(
        name="analyze_counter",
        agent="counter-negotiator",
        description=f"Analyze counter-offer for {property_address}. Our offer: ${our_offer:,.0f}. Counter: ${counter_price:,.0f}. Seller terms: {seller_terms}.",
        budget_tokens=25_000,
    ).task(
        name="market_context",
        agent="comp-analyzer",
        description=f"Update market context for {property_address}. Days on market, competing offers, price trajectory. Support negotiation strategy.",
        budget_tokens=15_000,
    ).task(
        name="recommend_response",
        agent="counter-negotiator",
        description=f"Recommend response strategy for {property_address} counter. Present options with trade-offs for buyer {buyer_id}.",
        budget_tokens=25_000,
        blocked_by=["analyze_counter", "market_context"],
    ).task(
        name="draft_response",
        agent="offer-drafter",
        description=f"Draft counter-offer response for {property_address} per buyer {buyer_id}'s chosen strategy. Requires buyer approval before sending.",
        budget_tokens=20_000,
        blocked_by=["recommend_response"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "property_address": property_address,
        "our_offer": our_offer,
        "counter_price": counter_price,
    }
    return workflow


def create_closing_workflow(
    buyer_id: str,
    property_address: str,
    purchase_price: float,
    closing_date: str,
) -> WorkflowDefinition:
    """Closing workflow — inspection, appraisal, title, final walkthrough, close."""
    builder = TaskGraphBuilder(f"Closing: {property_address} for {buyer_id}", "operational")

    builder.task(
        name="schedule_inspection",
        agent="inspection-coordinator",
        description=f"Schedule home inspection for {property_address}. Within contingency window. Buyer: {buyer_id}.",
        budget_tokens=10_000,
    ).task(
        name="review_inspection",
        agent="inspection-coordinator",
        description=f"Review inspection report for {property_address}. Summarize findings by severity. Estimate repair costs. Prepare repair request.",
        budget_tokens=20_000,
        blocked_by=["schedule_inspection"],
    ).task(
        name="escrow_tracking",
        agent="escrow-tracker",
        description=f"Track earnest money and escrow for {property_address}. Purchase: ${purchase_price:,.0f}. Monitor deposit, contingency releases, and closing funds.",
        budget_tokens=10_000,
    ).task(
        name="closing_coordination",
        agent="closing-coordinator",
        description=f"Coordinate closing for {property_address}. Price: ${purchase_price:,.0f}. Closing date: {closing_date}. Track title, loan approval, closing disclosure, and all deadlines.",
        budget_tokens=20_000,
        blocked_by=["review_inspection", "escrow_tracking"],
    ).task(
        name="final_walkthrough",
        agent="showing-scheduler",
        description=f"Schedule final walkthrough for {property_address} before closing on {closing_date}. Coordinate with buyer {buyer_id} and listing agent.",
        budget_tokens=10_000,
        blocked_by=["closing_coordination"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "property_address": property_address,
        "purchase_price": purchase_price,
        "closing_date": closing_date,
    }
    return workflow


def create_mortgage_prequalification_workflow(
    buyer_id: str,
    buyer_email: str,
    estimated_budget: float,
) -> WorkflowDefinition:
    """Mortgage pre-qualification workflow — connect with lenders, compare rates."""
    builder = TaskGraphBuilder(f"Mortgage Pre-Qual: {buyer_id}", "operational")

    builder.task(
        name="gather_financial_info",
        agent="support-agent",
        description=f"Gather basic financial info from buyer {buyer_id} ({buyer_email}) for mortgage pre-qualification. Income, debts, down payment estimate.",
        budget_tokens=10_000,
    ).task(
        name="lender_matching",
        agent="mortgage-connector",
        description=f"Match buyer {buyer_id} with mortgage lenders. Estimated budget: ${estimated_budget:,.0f}. Compare rates, terms, and programs.",
        budget_tokens=20_000,
        blocked_by=["gather_financial_info"],
    ).task(
        name="compliance_check",
        agent="compliance-agent",
        description=f"RESPA and TILA compliance check for mortgage referral process. Ensure no prohibited referral fees or kickbacks.",
        budget_tokens=10_000,
        blocked_by=["lender_matching"],
    ).task(
        name="send_options",
        agent="support-agent",
        description=f"Send mortgage lender options to buyer {buyer_id} ({buyer_email}). Include rate comparisons and next steps for pre-qualification.",
        budget_tokens=10_000,
        blocked_by=["compliance_check"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "buyer_email": buyer_email,
        "estimated_budget": estimated_budget,
    }
    return workflow


def create_buyer_onboarding_workflow(
    buyer_id: str,
    buyer_email: str,
    city: str,
    budget: float,
    plan: str = "starter",
) -> WorkflowDefinition:
    """Buyer onboarding workflow — setup, initial search, retainer."""
    builder = TaskGraphBuilder(f"Onboarding: {buyer_id}", "operational")

    builder.task(
        name="setup_profile",
        agent="support-agent",
        description=f"Set up buyer profile for {buyer_id} ({buyer_email}). City: {city}. Budget: ${budget:,.0f}. Plan: {plan}.",
        budget_tokens=10_000,
    ).task(
        name="initial_search",
        agent="mls-search",
        description=f"Run initial MLS search for new buyer {buyer_id} in {city}. Budget: ${budget:,.0f}. Show top 10 matches immediately.",
        budget_tokens=15_000,
        blocked_by=["setup_profile"],
    ).task(
        name="welcome_email",
        agent="support-agent",
        description=f"Send welcome email to {buyer_email} with initial property matches in {city}. Include guide to using HomeForge and next steps.",
        budget_tokens=10_000,
        blocked_by=["initial_search"],
    ).task(
        name="billing_setup",
        agent="fin-billing",
        description=f"Set up billing for buyer {buyer_id} ({buyer_email}). Plan: {plan}. Process retainer deposit if applicable.",
        budget_tokens=10_000,
        blocked_by=["setup_profile"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "buyer_id": buyer_id,
        "buyer_email": buyer_email,
        "city": city,
        "budget": budget,
        "plan": plan,
    }
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    target_markets: list[str],
) -> WorkflowDefinition:
    """Marketing campaign workflow for HomeForge buyer acquisition."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")
    markets = ", ".join(target_markets)

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}. Goal: {campaign_goal}. Budget: ${budget_usd:,.0f}. Target markets: {markets}.",
        budget_tokens=40_000,
    ).task(
        name="compliance_review",
        agent="compliance-agent",
        description=f"Review campaign strategy for Fair Housing Act compliance and real estate advertising regulations. Campaign: {campaign_name}.",
        budget_tokens=15_000,
        blocked_by=["strategy"],
    ).task(
        name="content",
        agent="mkt-content",
        description=f"Create content for campaign: {campaign_name}. Market reports, buyer guides, and savings calculators for: {markets}.",
        budget_tokens=60_000,
        blocked_by=["compliance_review"],
    ).task(
        name="ad_setup",
        agent="mkt-ppc",
        description=f"Set up Google Ads for campaign: {campaign_name}. Target home search terms in: {markets}. Budget: ${budget_usd:,.0f}.",
        budget_tokens=40_000,
        blocked_by=["compliance_review"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels in target markets.",
        budget_tokens=15_000,
        blocked_by=["content", "ad_setup"],
    ).task(
        name="analytics",
        agent="mkt-analytics",
        description=f"Track campaign: {campaign_name}. Monitor cost-per-signup, retainer conversion, and transaction completion against goal: {campaign_goal}.",
        budget_tokens=25_000,
        blocked_by=["launch"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "campaign_name": campaign_name,
        "budget_usd": budget_usd,
        "target_markets": target_markets,
    }
    return workflow
