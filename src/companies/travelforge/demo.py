"""
TravelForge AI demo scenarios.
"""

from __future__ import annotations


def run_demo():
    """Run a demo showcasing TravelForge AI capabilities."""
    from src.companies.travelforge.workflows import (
        create_trip_search_workflow,
        create_booking_workflow,
        create_cancellation_workflow,
        create_itinerary_optimization_workflow,
    )
    from src.mcp.custom_tools import CompanySystem
    from src.companies.travelforge.knowledge import seed_knowledge_base

    print("\n" + "=" * 70)
    print("  TravelForge AI — Demo Mode")
    print("  Search Everywhere, Book Directly, Skip the Middleman")
    print("=" * 70)

    system = CompanySystem()
    seed_knowledge_base(system.knowledge)

    # Demo 1: Trip Search Workflow
    print("\n Demo 1: Trip Search — NYC to Tokyo")
    print("-" * 50)
    wf = create_trip_search_workflow(
        user_id="user_77",
        origin="JFK",
        destination="Tokyo",
        depart_date="2026-04-15",
        return_date="2026-04-25",
        travelers=2,
        include_hotel=True,
        include_car=False,
        include_activities=True,
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")
    print(f"  (flights, hotels, activities searched in parallel)")

    # Demo 2: Booking Workflow
    print("\n Demo 2: Booking — Flight JFK→NRT")
    print("-" * 50)
    wf2 = create_booking_workflow(
        user_id="user_77",
        booking_type="flight",
        provider="ANA",
        item_id="ana_nh9_20260415",
        total_price=1249.00,
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: Itinerary Optimization
    print("\n Demo 3: Itinerary Optimization — Tokyo 10 Days")
    print("-" * 50)
    wf3 = create_itinerary_optimization_workflow(
        user_id="user_77",
        destination="Tokyo",
        travel_dates="2026-04-15 to 2026-04-25",
        budget_usd=3000.00,
        interests=["temples", "food tours", "anime", "onsen"],
    )
    print(f"  Created workflow: {wf3.name}")
    print(f"  Tasks: {len(wf3.tasks)}")
    ready3 = wf3.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready3]}")

    # Demo 4: Cancellation Workflow
    print("\n Demo 4: Cancellation — Hotel Booking")
    print("-" * 50)
    wf4 = create_cancellation_workflow(
        user_id="user_77",
        booking_id="bk_hotel_12345",
        booking_type="hotel",
        provider="Marriott",
        original_price=890.00,
    )
    print(f"  Created workflow: {wf4.name}")
    print(f"  Tasks: {len(wf4.tasks)}")
    ready4 = wf4.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready4]}")

    # Demo 5: HITL Approval — High-Value Refund
    print("\n Demo 5: HITL Approval — Flight Refund $1,249")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="refund-agent",
        department="support",
        category="refund",
        title="Refund request: ANA flight JFK→NRT — $1,249",
        description="User requesting full refund for ANA flight booked 3 days ago. "
                    "Within airline's 24h free cancellation window has passed. "
                    "Fare class shows non-refundable. Recommend offering travel credit "
                    "or rebooking assistance instead of cash refund.",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 6: Knowledge Base Query
    print("\n Demo 6: Knowledge Base — Compliance Rules")
    print("-" * 50)
    results = system.knowledge.search("compliance")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print("=" * 70 + "\n")
