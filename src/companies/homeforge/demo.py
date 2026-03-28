"""
HomeForge AI demo scenarios.
"""

from __future__ import annotations


def run_demo():
    """Run a demo showcasing HomeForge AI capabilities."""
    from src.companies.homeforge.workflows import (
        create_property_search_workflow,
        create_offer_workflow,
        create_closing_workflow,
        create_buyer_onboarding_workflow,
    )
    from src.mcp.custom_tools import CompanySystem
    from src.companies.homeforge.knowledge import seed_knowledge_base

    print("\n" + "=" * 70)
    print("  HomeForge AI — Demo Mode")
    print("  Buy Your Home with AI. Save the 3% Commission.")
    print("=" * 70)

    system = CompanySystem()
    seed_knowledge_base(system.knowledge)

    # Demo 1: Property Search
    print("\n Demo 1: Property Search — Austin, TX")
    print("-" * 50)
    wf = create_property_search_workflow(
        buyer_id="buyer_33",
        city="Austin, TX",
        max_price=550000,
        bedrooms=3,
        criteria="Good schools, within 20 min of downtown",
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")

    # Demo 2: Offer Workflow
    print("\n Demo 2: Offer — 123 Oak Lane")
    print("-" * 50)
    wf2 = create_offer_workflow(
        buyer_id="buyer_33",
        property_id="MLS_12345",
        property_address="123 Oak Lane, Austin, TX 78704",
        list_price=525000,
        offer_price=510000,
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: Closing Workflow
    print("\n Demo 3: Closing — 123 Oak Lane")
    print("-" * 50)
    wf3 = create_closing_workflow(
        buyer_id="buyer_33",
        property_address="123 Oak Lane, Austin, TX 78704",
        purchase_price=515000,
        closing_date="2026-04-30",
    )
    print(f"  Created workflow: {wf3.name}")
    print(f"  Tasks: {len(wf3.tasks)}")
    ready3 = wf3.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready3]}")

    # Demo 4: Buyer Onboarding
    print("\n Demo 4: New Buyer Onboarding — Full Service")
    print("-" * 50)
    wf4 = create_buyer_onboarding_workflow(
        buyer_id="buyer_new_12",
        buyer_email="lisa@example.com",
        city="Austin, TX",
        budget=600000,
        plan="full_service",
    )
    print(f"  Created workflow: {wf4.name}")
    print(f"  Tasks: {len(wf4.tasks)}")
    ready4 = wf4.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready4]}")

    # Demo 5: HITL Approval — Offer Submission
    print("\n Demo 5: HITL Approval — Offer Submission")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="offer-drafter",
        department="transaction",
        category="offer_submission",
        title="Submit offer: 123 Oak Lane — $510,000",
        description="Buyer buyer_33 has approved offer for 123 Oak Lane, Austin TX 78704. "
                    "List price: $525,000. Offer: $510,000 (3% below list). "
                    "Earnest money: $10,200 (2%). Inspection contingency: 10 days. "
                    "Financing contingency: 30 days. Closing: 45 days. "
                    "Comp analysis supports offer price (FMV range: $505K-$520K).",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 6: Knowledge Base Query
    print("\n Demo 6: Knowledge Base — Fair Housing Compliance")
    print("-" * 50)
    results = system.knowledge.search("fair housing")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print(f"  Commission saved example: $525K home × 3% = $15,750 saved!")
    print("=" * 70 + "\n")
