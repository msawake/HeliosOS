# Copyright 2024-2026 Awake Venture Studio, a Making Science Group company.
# SPDX-License-Identifier: BUSL-1.1
"""
LangChain agent with ForgeOS governance via HTTP (Mode C).

This example shows how to add ForgeOS governance to ANY existing
LangChain agent with ONE callback — no code changes to the agent itself.

The ForgeOSKernelCallback checks the ForgeOS kernel before every tool call:
  - ALLOW: tool executes normally
  - DENY: ToolException raised, LLM adapts naturally
  - RATE_LIMIT: ToolException raised with budget exceeded message

Requirements:
  pip install langchain-core langchain-openai httpx

Environment:
  OPENAI_API_KEY=sk-...
  FORGEOS_API_URL=https://forgeos-api.example.com
  FORGEOS_API_KEY=fos_your_key
"""

import asyncio
import os

# --- Your existing LangChain code (unchanged) ---

from langchain_core.tools import tool


@tool
def search_knowledge(query: str) -> str:
    """Search the company knowledge base."""
    return f"Found 3 results for: {query}"


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    return f"Email sent to {to}"


@tool
def record_metric(name: str, value: float) -> str:
    """Record a business metric."""
    return f"Recorded {name}={value}"


# --- Add ForgeOS governance (ONE import + ONE callback) ---

from stacks.langchain.callback import ForgeOSKernelCallback

callback = ForgeOSKernelCallback(
    forgeos_url=os.environ.get("FORGEOS_API_URL", "https://forgeos-api.example.com"),
    agent_id="langchain-assistant",
    api_key=os.environ.get("FORGEOS_API_KEY"),
)


async def main():
    """Run the agent with ForgeOS governance."""
    tools = [search_knowledge, send_email, record_metric]

    # Simulate tool calls with kernel checks
    for t in tools:
        print(f"\nCalling tool: {t.name}")
        try:
            # The callback fires on_tool_start before execution
            callback.on_tool_start(
                {"name": t.name, "description": t.description},
                "test input",
                run_id=__import__("uuid").uuid4(),
            )
            print(f"  Kernel: ALLOWED")
        except Exception as e:
            print(f"  Kernel: BLOCKED — {e}")
            continue

        # If we get here, the tool was allowed
        if t.name == "search_knowledge":
            result = t.invoke({"query": "AI trends"})
        elif t.name == "send_email":
            result = t.invoke({"to": "test@example.com", "subject": "Test", "body": "Hello"})
        else:
            result = t.invoke({"name": "leads_found", "value": 42.0})
        print(f"  Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
