"""
Helios OS Hello World — deploy and invoke via Python SDK.

Usage:
    PYTHONPATH=. python examples/forgeos/hello_world.py
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient


class HelloForgeOS(Agent):
    name = "hello-forgeos"
    description = "Hello World on Helios OS native stack"
    stack = "forgeos"
    execution_type = "reflex"
    model = "gpt-4o"
    provider = "openai"
    system_prompt = (
        "You are a friendly hello-world agent running on the Helios OS native stack. "
        "When greeted, introduce yourself and explain which stack you're running on. "
        "Keep responses short (2-3 sentences)."
    )


def main():
    manifest = HelloForgeOS.manifest()
    print(f"[Helios OS] Deploying '{manifest.metadata.name}'...")

    with ForgeOSClient(base_url="http://localhost:5000") as client:
        try:
            agent_id = client.deploy(manifest)
            print(f"[Helios OS] Deployed: {agent_id}")
        except Exception as e:
            if "already exists" in str(e):
                print(f"[Helios OS] Already deployed, invoking...")
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == "hello-forgeos"),
                    "hello-forgeos",
                )
            else:
                raise

        print(f"\n[Helios OS] Invoking with 'Hello!'...")
        result = client.invoke(agent_id, "Hello! Who are you?")
        print(f"[Helios OS] Status: {result.get('status')}")
        print(f"[Helios OS] Response: {result.get('result', '')[:500]}")
        if result.get("warnings"):
            print(f"[Helios OS] Warnings: {result['warnings']}")
        print(f"[Helios OS] Tokens used: {result.get('tokens_used', 0)}")


if __name__ == "__main__":
    main()
