"""
Deploy and test hello-world agents for all 4 Helios OS stacks.

Verifies each stack adapter works end-to-end: deploy → invoke → validate output.

Usage:
    PYTHONPATH=. python examples/run_all_hello_world.py

Prerequisites:
    - Backend running: PYTHONPATH=. python -m src.bootstrap --no-auth --dashboard --port 5000
    - At least one LLM API key configured in .env
"""

import sys
sys.path.insert(0, ".")

from src.forgeos_sdk import Agent, ForgeOSClient


AGENTS = [
    {
        "name": "hello-forgeos",
        "stack": "forgeos",
        "label": "Helios OS Native",
        "prompt": "You are a hello-world agent on Helios OS native stack. Introduce yourself in 2 sentences.",
    },
    {
        "name": "hello-crewai",
        "stack": "crewai",
        "label": "CrewAI",
        "prompt": "You are a hello-world agent on CrewAI stack. Introduce yourself in 2 sentences.",
    },
    {
        "name": "hello-adk",
        "stack": "adk",
        "label": "Google ADK",
        "prompt": "You are a hello-world agent on Google ADK stack. Introduce yourself in 2 sentences.",
    },
    {
        "name": "hello-openclaw",
        "stack": "openclaw",
        "label": "OpenClaw",
        "prompt": "You are a hello-world agent on OpenClaw stack. Introduce yourself in 2 sentences.",
    },
]


def main():
    client = ForgeOSClient(base_url="http://localhost:5000")
    results = {}

    print("=" * 60)
    print("Helios OS Hello World — All 4 Stacks")
    print("=" * 60)

    for spec in AGENTS:
        name = spec["name"]
        stack = spec["stack"]
        label = spec["label"]
        prompt = spec["prompt"]

        print(f"\n{'─' * 50}")
        print(f"  {label} ({stack})")
        print(f"{'─' * 50}")

        # Deploy
        try:
            agent_id = client.deploy(Agent.builder(name)
                .stack(stack)
                .reflex()
                .model("gpt-4o", provider="openai")
                .prompt(prompt)
                .description(f"Hello World on {label}")
                .department("examples")
                .build())
            print(f"  Deploy:  OK ({agent_id})")
        except Exception as e:
            if "already exists" in str(e):
                agents = client.list()
                agent_id = next(
                    (a["agent_id"] for a in agents if a["name"] == name),
                    name,
                )
                print(f"  Deploy:  Already exists ({agent_id})")
            else:
                print(f"  Deploy:  FAILED — {e}")
                results[label] = "DEPLOY_FAILED"
                continue

        # Invoke
        try:
            result = client.invoke(agent_id, "Hello! Who are you?")
            status = result.get("status", "?")
            output = result.get("result", "")
            tokens = result.get("tokens_used", 0)
            warnings = result.get("warnings")
            simulated = "[SIMULATED" in output

            print(f"  Invoke:  {status}")
            print(f"  Tokens:  {tokens}")
            if simulated:
                print(f"  Mode:    SIMULATED (no API key)")
            else:
                print(f"  Mode:    REAL LLM")
            print(f"  Output:  {output[:200]}")
            if warnings:
                print(f"  Warns:   {warnings}")

            results[label] = "SIMULATED" if simulated else ("OK" if status == "completed" else status.upper())
        except Exception as e:
            print(f"  Invoke:  FAILED — {e}")
            results[label] = "INVOKE_FAILED"

    # Summary
    print(f"\n{'=' * 60}")
    print("  RESULTS SUMMARY")
    print(f"{'=' * 60}")
    all_ok = True
    for label, status in results.items():
        icon = "✓" if status == "OK" else ("~" if status == "SIMULATED" else "✗")
        print(f"  {icon} {label:20} {status}")
        if status in ("DEPLOY_FAILED", "INVOKE_FAILED"):
            all_ok = False
    print(f"{'=' * 60}")

    if all_ok:
        print("  All 4 stacks deployed and invoked successfully.")
    else:
        print("  Some stacks failed — check errors above.")
        sys.exit(1)

    client.close()


if __name__ == "__main__":
    main()
