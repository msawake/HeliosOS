"""Demo scenario for practical agents."""

from __future__ import annotations


def run_demo(invoker, workflow_engine, system):
    """Run a demo showcasing practical agents."""
    import asyncio

    async def _demo():
        print("\n=== PRACTICAL AGENTS DEMO ===\n")

        # 1. Email triage
        print("1. Running email-triage...")
        result = await invoker.invoke("email-triage",
            "Classify these emails:\n"
            "- From: newsletter@techcrunch.com, Subject: 'Daily Digest'\n"
            "- From: ceo@client.com, Subject: 'URGENT: Contract review needed'\n"
            "- From: john@vendor.com, Subject: 'Meeting tomorrow at 2pm?'\n"
        )
        print(f"   Status: {result.status.value}")
        print(f"   Result: {(result.result or '')[:200]}\n")

        # 2. Standup digest
        print("2. Running standup-digest...")
        result = await invoker.invoke("standup-digest",
            "Summarize these standups:\n"
            "Alice: Shipped the new login page. Working on password reset. Blocked on API docs.\n"
            "Bob: Reviewed 3 PRs. Starting database migration today.\n"
            "Carol: No update posted.\n"
        )
        print(f"   Status: {result.status.value}")
        print(f"   Result: {(result.result or '')[:200]}\n")

        # 3. Contract checker
        print("3. Running contract-checker...")
        result = await invoker.invoke("contract-checker",
            "Review this contract excerpt:\n"
            "Section 4.1: Auto-renewal. This agreement automatically renews for successive "
            "1-year terms unless cancelled with 90 days written notice.\n"
            "Section 7.2: Liability. Vendor's total liability shall not exceed $500.\n"
            "Section 9.1: IP Assignment. Client assigns all work product IP to Vendor.\n"
        )
        print(f"   Status: {result.status.value}")
        print(f"   Result: {(result.result or '')[:300]}\n")

        print("=== DEMO COMPLETE ===")

    asyncio.run(_demo())
