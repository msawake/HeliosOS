"""
TravelForge AI workflow templates.

6 pre-built workflow definitions for TravelForge's travel booking operations.
"""

from __future__ import annotations

from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    WorkflowDefinition,
)


def create_trip_search_workflow(
    user_id: str,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    travelers: int = 1,
    include_hotel: bool = True,
    include_car: bool = False,
    include_activities: bool = False,
) -> WorkflowDefinition:
    """Trip search workflow — search flights, hotels, cars, activities in parallel."""
    builder = TaskGraphBuilder(f"Trip Search: {origin} → {destination} for {user_id}", "operational")

    builder.task(
        name="search_flights",
        agent="search-flight",
        description=f"Search flights from {origin} to {destination}. Depart: {depart_date}. Return: {return_date or 'one-way'}. Travelers: {travelers}.",
        budget_tokens=15_000,
    )

    if include_hotel:
        builder.task(
            name="search_hotels",
            agent="search-hotel",
            description=f"Search hotels in {destination}. Check-in: {depart_date}. Check-out: {return_date or 'open'}. Guests: {travelers}.",
            budget_tokens=15_000,
        )

    if include_car:
        builder.task(
            name="search_cars",
            agent="search-car",
            description=f"Search car rentals in {destination}. Pickup: {depart_date}. Return: {return_date or 'open'}.",
            budget_tokens=10_000,
        )

    if include_activities:
        builder.task(
            name="search_activities",
            agent="search-activity",
            description=f"Search activities and tours in {destination}. Dates: {depart_date} to {return_date or 'open'}. Travelers: {travelers}.",
            budget_tokens=10_000,
        )

    # Price comparison runs after all searches complete
    blocked_by = ["search_flights"]
    if include_hotel:
        blocked_by.append("search_hotels")
    if include_car:
        blocked_by.append("search_cars")
    if include_activities:
        blocked_by.append("search_activities")

    builder.task(
        name="compare_prices",
        agent="compare-prices",
        description=f"Compare and rank all search results for {origin} → {destination}. Find best value combinations across providers.",
        budget_tokens=20_000,
        blocked_by=blocked_by,
    ).task(
        name="build_itinerary",
        agent="itinerary-planner",
        description=f"Build optimized itinerary options for user {user_id} traveling {origin} → {destination}. Combine best-value flights, hotels, and activities.",
        budget_tokens=20_000,
        blocked_by=["compare_prices"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "origin": origin,
        "destination": destination,
        "depart_date": depart_date,
        "return_date": return_date,
        "travelers": travelers,
    }
    return workflow


def create_booking_workflow(
    user_id: str,
    booking_type: str,
    provider: str,
    item_id: str,
    total_price: float,
) -> WorkflowDefinition:
    """Booking execution workflow — compliance check, book, confirm."""
    builder = TaskGraphBuilder(f"Booking: {booking_type} for {user_id}", "operational")

    builder.task(
        name="compliance_check",
        agent="compliance-agent",
        description=f"Pre-booking compliance check for {booking_type} booking. Provider: {provider}. Item: {item_id}. Price: ${total_price}. Verify pricing display, cancellation terms, and PCI readiness.",
        budget_tokens=10_000,
    ).task(
        name="execute_booking",
        agent="book-agent",
        description=f"Execute {booking_type} booking for user {user_id} with {provider}. Item: {item_id}. Total: ${total_price}. Process payment via Stripe and obtain confirmation.",
        budget_tokens=15_000,
        blocked_by=["compliance_check"],
    ).task(
        name="send_confirmation",
        agent="book-agent",
        description=f"Send booking confirmation to user {user_id}. Include booking reference, itinerary details, cancellation policy, and receipt.",
        budget_tokens=10_000,
        blocked_by=["execute_booking"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "booking_type": booking_type,
        "provider": provider,
        "item_id": item_id,
        "total_price": total_price,
    }
    return workflow


def create_price_monitor_workflow(
    user_id: str,
    search_id: str,
    route_or_hotel: str,
    target_price: float,
) -> WorkflowDefinition:
    """Price monitoring workflow — track prices and alert on drops."""
    builder = TaskGraphBuilder(f"Price Monitor: {route_or_hotel} for {user_id}", "operational")

    builder.task(
        name="fetch_current_prices",
        agent="compare-prices",
        description=f"Fetch current prices for '{route_or_hotel}' (search {search_id}). Compare against target price ${target_price}.",
        budget_tokens=15_000,
    ).task(
        name="analyze_trend",
        agent="compare-prices",
        description=f"Analyze price trend for '{route_or_hotel}'. Predict if prices are likely to drop further or if user should book now.",
        budget_tokens=15_000,
        blocked_by=["fetch_current_prices"],
    ).task(
        name="notify_user",
        agent="book-agent",
        description=f"Notify user {user_id} about price status for '{route_or_hotel}'. Include current best price, trend prediction, and book-now link if target ${target_price} met.",
        budget_tokens=10_000,
        blocked_by=["analyze_trend"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "search_id": search_id,
        "route_or_hotel": route_or_hotel,
        "target_price": target_price,
    }
    return workflow


def create_cancellation_workflow(
    user_id: str,
    booking_id: str,
    booking_type: str,
    provider: str,
    original_price: float,
) -> WorkflowDefinition:
    """Cancellation workflow — calculate refund, process cancel, confirm."""
    builder = TaskGraphBuilder(f"Cancellation: {booking_type} {booking_id} for {user_id}", "operational")

    builder.task(
        name="calculate_refund",
        agent="refund-agent",
        description=f"Calculate refund for {booking_type} booking {booking_id} with {provider}. Original price: ${original_price}. Check cancellation window and policy.",
        budget_tokens=10_000,
    ).task(
        name="process_cancellation",
        agent="change-agent",
        description=f"Process cancellation of {booking_type} booking {booking_id} with {provider} for user {user_id}. Execute cancellation and initiate refund.",
        budget_tokens=15_000,
        blocked_by=["calculate_refund"],
    ).task(
        name="send_confirmation",
        agent="refund-agent",
        description=f"Send cancellation confirmation to user {user_id}. Include refund amount, processing timeline, and booking {booking_id} final status.",
        budget_tokens=10_000,
        blocked_by=["process_cancellation"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "booking_id": booking_id,
        "booking_type": booking_type,
        "provider": provider,
        "original_price": original_price,
    }
    return workflow


def create_itinerary_optimization_workflow(
    user_id: str,
    destination: str,
    travel_dates: str,
    budget_usd: float,
    interests: list[str],
) -> WorkflowDefinition:
    """Itinerary optimization workflow — build a complete, optimized trip plan."""
    builder = TaskGraphBuilder(f"Itinerary Optimization: {destination} for {user_id}", "operational")
    interest_str = ", ".join(interests)

    builder.task(
        name="search_activities",
        agent="search-activity",
        description=f"Search activities in {destination} matching interests: {interest_str}. Dates: {travel_dates}.",
        budget_tokens=15_000,
    ).task(
        name="price_optimize",
        agent="compare-prices",
        description=f"Find best prices for activities and dining in {destination}. Budget: ${budget_usd}. Dates: {travel_dates}.",
        budget_tokens=15_000,
    ).task(
        name="build_itinerary",
        agent="itinerary-planner",
        description=f"Build day-by-day itinerary for {destination}. Dates: {travel_dates}. Budget: ${budget_usd}. Interests: {interest_str}. Optimize for logistics and experience.",
        budget_tokens=25_000,
        blocked_by=["search_activities", "price_optimize"],
    ).task(
        name="compliance_review",
        agent="compliance-agent",
        description=f"Review itinerary for {destination}. Check visa requirements, travel advisories, and booking compliance for recommended activities.",
        budget_tokens=10_000,
        blocked_by=["build_itinerary"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "user_id": user_id,
        "destination": destination,
        "travel_dates": travel_dates,
        "budget_usd": budget_usd,
        "interests": interests,
    }
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    target_destinations: list[str],
) -> WorkflowDefinition:
    """Marketing campaign workflow for TravelForge user acquisition."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")
    dest_str = ", ".join(target_destinations)

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}. Goal: {campaign_goal}. Budget: ${budget_usd}. Target destinations: {dest_str}.",
        budget_tokens=40_000,
    ).task(
        name="compliance_review",
        agent="compliance-agent",
        description=f"Review campaign strategy for DOT advertising compliance and consumer protection requirements. Campaign: {campaign_name}.",
        budget_tokens=15_000,
        blocked_by=["strategy"],
    ).task(
        name="content",
        agent="mkt-content",
        description=f"Create content for campaign: {campaign_name}. Destination landing pages, deal alerts, and social content for: {dest_str}.",
        budget_tokens=60_000,
        blocked_by=["compliance_review"],
    ).task(
        name="ad_setup",
        agent="mkt-ppc",
        description=f"Set up Google Ads for campaign: {campaign_name}. Target route-based search terms for: {dest_str}. Budget: ${budget_usd}.",
        budget_tokens=40_000,
        blocked_by=["compliance_review"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels.",
        budget_tokens=15_000,
        blocked_by=["content", "ad_setup"],
    ).task(
        name="analytics",
        agent="mkt-analytics",
        description=f"Track campaign: {campaign_name}. Monitor CAC, booking conversion, and revenue against goal: {campaign_goal}.",
        budget_tokens=25_000,
        blocked_by=["launch"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "campaign_name": campaign_name,
        "budget_usd": budget_usd,
        "target_destinations": target_destinations,
    }
    return workflow
