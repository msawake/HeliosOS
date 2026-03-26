"""
LeadForge AI demo scenarios.
"""

from __future__ import annotations


def run_demo():
    """Run a demo showcasing LeadForge AI capabilities."""
    from src.companies.leadforge.workflows import (
        create_lead_qualification_workflow,
        create_client_onboarding_workflow,
    )
    from src.mcp.custom_tools import CompanySystem
    from src.companies.leadforge.knowledge import seed_knowledge_base

    print("\n" + "=" * 70)
    print("  LeadForge AI — Demo Mode")
    print("  AI-Powered B2B Lead Generation Agency")
    print("=" * 70)

    system = CompanySystem()
    seed_knowledge_base(system.knowledge)

    # Demo 1: Lead Qualification Workflow
    print("\n Demo 1: Lead Qualification Workflow")
    print("-" * 50)
    wf = create_lead_qualification_workflow(
        prospect_name="Sarah Chen",
        prospect_email="sarah.chen@techcorp.com",
        prospect_company="TechCorp",
        client_name="Acme SaaS",
        source="inbound",
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")

    # Demo 2: Client Onboarding Workflow
    print("\n Demo 2: Client Onboarding Workflow")
    print("-" * 50)
    wf2 = create_client_onboarding_workflow(
        client_name="Acme SaaS",
        client_contact_email="cto@acmesaas.com",
        retainer_amount_usd=5000,
        services=["outbound email", "LinkedIn outreach", "lead scoring"],
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: HITL Approval Request
    print("\n Demo 3: HITL Approval — Google Ads Budget Increase")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="mkt-ppc",
        department="marketing",
        category="ad_spend",
        title="Increase Google Ads daily budget from $200 to $350",
        description="Campaign 'B2B Lead Gen — Non-Brand' showing strong ROAS of 4.2x. "
                    "Recommend increasing daily budget by 75% to capture more impression share. "
                    "Expected additional spend: $4,500/month.",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 4: Cross-Department Event
    print("\n Demo 4: Cross-Department Event — Sales -> Marketing")
    print("-" * 50)
    event_id = system.event_bus.publish(
        source_agent="sales-lead",
        source_department="sales",
        target_department="marketing",
        event_type="REQUEST",
        category="CONTENT_REQUEST",
        payload={
            "client": "Acme SaaS",
            "request": "Need case study for enterprise SaaS prospects",
            "target_persona": "VP of Sales at B2B SaaS companies",
            "deadline": "2026-03-15",
        },
        priority="P2_MEDIUM",
    )
    events = system.event_bus.query(target_department="marketing")
    print(f"  Event published: {event_id[:8]}...")
    print(f"  Pending marketing events: {len(events)}")

    # Demo 5: Knowledge Base Query
    print("\n Demo 5: Knowledge Base — Lead Scoring Criteria")
    print("-" * 50)
    results = system.knowledge.search("lead scoring qualification")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print("=" * 70 + "\n")
