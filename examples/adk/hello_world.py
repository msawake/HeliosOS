"""
Google ADK Hello World — deploy and invoke via Python SDK.

If the ADK SDK is installed (`pip install google-adk`), the agent runs through
the real ADK Runner.run_async() path. Otherwise, falls back to Helios OS native.

Usage:
    PYTHONPATH=. python examples/adk/hello_world.py
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient


class HelloADK(Agent):
    name = "hello-adk"
    description = "Hello World on Google ADK stack"
    stack = "adk"
    execution_type = "reflex"
    model = "gpt-4o"
    provider = "openai"
    system_prompt = (
        "You are a friendly hello-world agent running on the Google ADK stack. "
        "When greeted, introduce yourself and explain which stack you're running on. "
        "Mention that ADK supports Gemini models natively but you can also use Claude or GPT. "
        "Keep responses short (2-3 sentences)."
    )


def main():
    manifest = HelloADK.manifest()
    print(f"[ADK] Deploying '{manifest.metadata.name}'...")

    with ForgeOSClient(base_url="http://localhost:5000") as client:
        try:
            agent_id = client.deploy(manifest)
            print(f"[ADK] Deployed: {agent_id}")
        except Exception as e:
            if "already exists" in str(e):
                print(f"[ADK] Already deployed, invoking...")
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == "hello-adk"),
                    "hello-adk",
                )
            else:
                raise

        print(f"\n[ADK] Invoking with 'Hello!'...")
        result = client.invoke(agent_id, "Hello! Who are you and what framework are you running on?")
        print(f"[ADK] Status: {result.get('status')}")
        print(f"[ADK] Response: {result.get('result', '')[:500]}")
        if result.get("warnings"):
            print(f"[ADK] Warnings: {result['warnings']}")
        print(f"[ADK] Tokens used: {result.get('tokens_used', 0)}")


if __name__ == "__main__":
    main()
