"""
InsureForge AI demo scenarios.
"""

from __future__ import annotations


def run_demo():
    """Run a demo showcasing InsureForge AI capabilities."""
    from src.companies.insureforge.workflows import (
        create_quote_comparison_workflow,
        create_application_workflow,
        create_claims_support_workflow,
        create_customer_onboarding_workflow,
    )
    from src.mcp.custom_tools import CompanySystem
    from src.companies.insureforge.knowledge import seed_knowledge_base

    print("\n" + "=" * 70)
    print("  InsureForge AI — Demo Mode")
    print("  Compare 20+ Carriers in Minutes. No Calls, No Forms, No Commission.")
    print("=" * 70)

    system = CompanySystem()
    seed_knowledge_base(system.knowledge)

    # Demo 1: Quote Comparison Workflow
    print("\n Demo 1: Quote Comparison — Auto Insurance in Texas")
    print("-" * 50)
    wf = create_quote_comparison_workflow(
        user_id="user_88",
        insurance_type="auto",
        state="TX",
        coverage_level="recommended",
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")

    # Demo 2: Application Workflow
    print("\n Demo 2: Application — GEICO Auto Policy")
    print("-" * 50)
    wf2 = create_application_workflow(
        user_id="user_88",
        carrier="GEICO",
        insurance_type="auto",
        policy_id="geico_auto_2026_tx",
        premium=142.50,
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: Claims Support
    print("\n Demo 3: Claims Support — Fender Bender")
    print("-" * 50)
    wf3 = create_claims_support_workflow(
        user_id="user_88",
        policy_id="geico_auto_2026_tx",
        carrier="GEICO",
        claim_type="collision",
        description="Minor fender bender in parking lot. No injuries. Estimate ~$2,500 damage.",
    )
    print(f"  Created workflow: {wf3.name}")
    print(f"  Tasks: {len(wf3.tasks)}")
    ready3 = wf3.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready3]}")

    # Demo 4: Customer Onboarding
    print("\n Demo 4: New Customer Onboarding")
    print("-" * 50)
    wf4 = create_customer_onboarding_workflow(
        user_id="user_new_55",
        user_email="mike@example.com",
        insurance_interests=["auto", "home"],
        state="CA",
    )
    print(f"  Created workflow: {wf4.name}")
    print(f"  Tasks: {len(wf4.tasks)}")
    ready4 = wf4.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready4]}")

    # Demo 5: HITL Approval — Policy Binding
    print("\n Demo 5: HITL Approval — Policy Binding Review")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="application-agent",
        department="analysis",
        category="policy_binding",
        title="Bind auto policy with GEICO for user_88 — $142.50/month",
        description="User user_88 has confirmed application for GEICO auto policy "
                    "in Texas. Premium: $142.50/month. Coverage: 100/300/100 liability, "
                    "$500 comprehensive deductible, $500 collision deductible. "
                    "Profile verified, compliance check passed.",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 6: Knowledge Base Query
    print("\n Demo 6: Knowledge Base — Referral Fee Model")
    print("-" * 50)
    results = system.knowledge.search("referral")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print("=" * 70 + "\n")
