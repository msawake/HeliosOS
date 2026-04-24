"""
Undeploy all example agents from the platform.

Usage:
    PYTHONPATH=. python examples/cleanup.py
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import ForgeOSClient


def main():
    client = ForgeOSClient(base_url="http://localhost:5000")
    agents = client.list()

    # Find agents that belong to examples (department=examples or name matches patterns)
    example_agents = [
        a for a in agents
        if a.get("department") == "examples"
        or a.get("name", "").startswith("hello-")
        or any(a.get("name", "").startswith(p) for p in [
            "qa-", "daily-", "system-", "alert-", "trend-", "crew-",
            "ceo-", "research-", "review-", "escalation-",
            "lead-", "web-", "ad-", "property-", "insurance-", "pr-",
            "file-", "report-", "log-", "config-",
            "full-stack-", "compliance-", "onboarding-", "budget-",
            "self-improving", "multi-agent",
        ])
    ]

    if not example_agents:
        print("No example agents found.")
        return

    print(f"Undeploying {len(example_agents)} example agents:\n")
    for a in example_agents:
        try:
            client.undeploy(a["agent_id"])
            print(f"  Undeployed: {a['name']} ({a['agent_id']})")
        except Exception as e:
            print(f"  Failed: {a['name']} — {e}")

    client.close()
    print(f"\nDone. {len(example_agents)} agents cleaned up.")


if __name__ == "__main__":
    main()
