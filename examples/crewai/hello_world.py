"""
CrewAI Hello World — deploy and invoke via Python SDK.

If the CrewAI SDK is installed (`pip install crewai`), the agent runs through
the real Crew.kickoff() path. Otherwise, it falls back to the Helios OS native
agentic loop (same output, different runtime).

Usage:
    PYTHONPATH=. python examples/crewai/hello_world.py
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient


class HelloCrewAI(Agent):
    name = "hello-crewai"
    description = "Hello World on CrewAI stack (role-based)"
    stack = "crewai"
    execution_type = "reflex"
    model = "gpt-4o"
    provider = "openai"
    system_prompt = (
        "You are a friendly hello-world agent running on the CrewAI stack. "
        "Your role is 'Greeter' and your goal is to welcome users warmly. "
        "When greeted, introduce yourself and explain which stack you're running on. "
        "Keep responses short (2-3 sentences)."
    )


def main():
    manifest = HelloCrewAI.manifest()
    print(f"[CrewAI] Deploying '{manifest.metadata.name}'...")

    with ForgeOSClient(base_url="http://localhost:5000") as client:
        try:
            agent_id = client.deploy(manifest)
            print(f"[CrewAI] Deployed: {agent_id}")
        except Exception as e:
            if "already exists" in str(e):
                print(f"[CrewAI] Already deployed, invoking...")
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == "hello-crewai"),
                    "hello-crewai",
                )
            else:
                raise

        print(f"\n[CrewAI] Invoking with 'Hello!'...")
        result = client.invoke(agent_id, "Hello! Who are you and what framework are you running on?")
        print(f"[CrewAI] Status: {result.get('status')}")
        print(f"[CrewAI] Response: {result.get('result', '')[:500]}")
        if result.get("warnings"):
            print(f"[CrewAI] Warnings: {result['warnings']}")
        print(f"[CrewAI] Tokens used: {result.get('tokens_used', 0)}")


if __name__ == "__main__":
    main()
