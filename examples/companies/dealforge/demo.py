"""
DealForge AI demo scenarios.
"""

from __future__ import annotations


def run_demo():
    """Run a demo showcasing DealForge AI capabilities."""
    from src.companies.dealforge.workflows import (
        create_deal_search_workflow,
        create_deal_negotiation_workflow,
        create_fraud_check_workflow,
        create_user_onboarding_workflow,
    )
    from src.mcp.custom_tools import CompanySystem
    from src.companies.dealforge.knowledge import seed_knowledge_base

    print("\n" + "=" * 70)
    print("  DealForge AI — Demo Mode")
    print("  Your AI Deal-Finder Across All Marketplaces")
    print("=" * 70)

    system = CompanySystem()
    seed_knowledge_base(system.knowledge)

    # Demo 1: Deal Search Workflow
    print("\n Demo 1: Deal Search — Used iPhone in Austin")
    print("-" * 50)
    wf = create_deal_search_workflow(
        user_id="user_42",
        search_query="iPhone 15 Pro",
        category="electronics",
        max_price=800.00,
        location="Austin, TX",
        radius_miles=30,
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")
    print(f"  (4 crawlers run in parallel across marketplaces)")

    # Demo 2: Deal Negotiation Workflow
    print("\n Demo 2: Negotiation Assist — Vintage Couch")
    print("-" * 50)
    wf2 = create_deal_negotiation_workflow(
        user_id="user_42",
        listing_id="cl_98765",
        listing_title="Mid-Century Modern Couch",
        asking_price=450.00,
        target_price=350.00,
        platform="craigslist",
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: Fraud Check Workflow
    print("\n Demo 3: Fraud Investigation — Suspicious Listing")
    print("-" * 50)
    wf3 = create_fraud_check_workflow(
        listing_id="fb_55443",
        listing_title="MacBook Pro M3 - $200 MUST SELL TODAY",
        platform="facebook_marketplace",
        reported_by="fraud-detector",
    )
    print(f"  Created workflow: {wf3.name}")
    print(f"  Tasks: {len(wf3.tasks)}")
    ready3 = wf3.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready3]}")

    # Demo 4: User Onboarding Workflow
    print("\n Demo 4: New User Onboarding — Pro Plan")
    print("-" * 50)
    wf4 = create_user_onboarding_workflow(
        user_id="user_new_99",
        user_email="jane@example.com",
        plan="pro",
        interests=["electronics", "furniture", "cars"],
    )
    print(f"  Created workflow: {wf4.name}")
    print(f"  Tasks: {len(wf4.tasks)}")
    ready4 = wf4.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready4]}")

    # Demo 5: HITL Approval — Fraud Review
    print("\n Demo 5: HITL Approval — Fraud Review Escalation")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="fraud-detector",
        department="deals",
        category="fraud_review",
        title="Suspicious listing cluster — 5 identical PS5 listings from different sellers",
        description="Detected 5 PS5 listings at $150 each (70% below FMV) posted within "
                    "2 hours from accounts created in the last 3 days. Cross-platform "
                    "duplicate images confirmed. Recommend suppressing all 5 listings.",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 6: Knowledge Base Query
    print("\n Demo 6: Knowledge Base — Deal Scoring Rules")
    print("-" * 50)
    results = system.knowledge.search("deal scoring pricing hot")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print("=" * 70 + "\n")
