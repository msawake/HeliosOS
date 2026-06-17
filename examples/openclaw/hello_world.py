"""
OpenClaw Hello World — deploy and invoke via Python SDK.

If the OpenClaw gateway (openclaw2/) is available, the agent runs through
the real HTTP gateway. Otherwise, falls back to Helios OS native agentic loop.

Usage:
    PYTHONPATH=. python examples/openclaw/hello_world.py
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient


class HelloOpenClaw(Agent):
    name = "hello-openclaw"
    description = "Hello World on OpenClaw stack (markdown-first)"
    stack = "openclaw"
    execution_type = "reflex"
    model = "gpt-4o"
    provider = "openai"
    system_prompt = (
        "You are a friendly hello-world agent running on the OpenClaw stack. "
        "When greeted, introduce yourself and explain which stack you're running on. "
        "Mention that OpenClaw uses a markdown-first approach with SOUL.md files. "
        "Keep responses short (2-3 sentences)."
    )


def main():
    manifest = HelloOpenClaw.manifest()
    print(f"[OpenClaw] Deploying '{manifest.metadata.name}'...")

    with ForgeOSClient(base_url="http://localhost:5000") as client:
        try:
            agent_id = client.deploy(manifest)
            print(f"[OpenClaw] Deployed: {agent_id}")
        except Exception as e:
            if "already exists" in str(e):
                print(f"[OpenClaw] Already deployed, invoking...")
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == "hello-openclaw"),
                    "hello-openclaw",
                )
            else:
                raise

        print(f"\n[OpenClaw] Invoking with 'Hello!'...")
        result = client.invoke(agent_id, "Hello! Who are you and what framework are you running on?")
        print(f"[OpenClaw] Status: {result.get('status')}")
        print(f"[OpenClaw] Response: {result.get('result', '')[:500]}")
        if result.get("warnings"):
            print(f"[OpenClaw] Warnings: {result['warnings']}")
        print(f"[OpenClaw] Tokens used: {result.get('tokens_used', 0)}")


if __name__ == "__main__":
    main()
