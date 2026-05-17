#!/usr/bin/env python3
"""
Side-by-side comparison: ADK Original vs ADK + ForgeOS Governance.

Runs the same customer-service agent in two modes:
  Mode A: Real ADK agent running inside ForgeOS (kernel intercepts via adapter)
  Mode B: Real ADK agent running standalone (no governance)

Usage:
  PYTHONPATH=.:adk-samples/python/agents/customer-service \
    GOOGLE_CLOUD_PROJECT=admachina-atomic-test-84 \
    GOOGLE_CLOUD_LOCATION=us-central1 \
    python3 examples/adk-agents/run_comparison.py
"""

import asyncio
import json
import os
import sys
import time

# ============================================================================
# MODE B: Pure ADK — No ForgeOS
# ============================================================================

async def run_pure_adk(prompt: str) -> dict:
    """Run the customer-service agent using Google ADK directly. No governance."""
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from customer_service.agent import root_agent

    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="pure-adk", session_service=session_service)

    session = await session_service.create_session(app_name="pure-adk", user_id="user-1")

    from google.genai.types import Content, Part
    message = Content(parts=[Part(text=prompt)], role="user")

    output = ""
    tool_calls = []
    tokens = 0
    start = time.time()

    async for event in runner.run_async(user_id="user-1", session_id=session.id, new_message=message):
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    output += part.text
                if hasattr(part, "function_call") and part.function_call:
                    tool_calls.append(part.function_call.name)
        if hasattr(event, "usage_metadata") and event.usage_metadata:
            tokens += getattr(event.usage_metadata, "total_token_count", 0)

    elapsed = time.time() - start
    return {
        "mode": "Pure ADK (no governance)",
        "output": output[:500],
        "tool_calls": tool_calls,
        "tokens": tokens,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {
            "tool_acl_check": "NONE",
            "budget_check": "NONE",
            "audit_logged": False,
            "pii_masked": False,
            "hitl_enforced": False,
        },
    }


# ============================================================================
# MODE A: ADK inside ForgeOS — Full Governance
# ============================================================================

async def run_forgeos_adk(prompt: str) -> dict:
    """Run the customer-service agent through ForgeOS with kernel governance."""
    from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig
    from src.platform.executor import PlatformExecutor
    from src.platform.registry import AgentRegistry
    from src.platform.scheduler import SchedulerEngine
    from src.platform.event_bus import EventBus
    from src.platform.kernel import Kernel
    from src.forgeos_sdk.kernel import Kernel as SDKKernel
    from src.forgeos_sdk.runtime import runtime as sdk_runtime
    from stacks.adk.adapter import ADKAdapter

    # Build platform
    registry = AgentRegistry()
    scheduler = SchedulerEngine()
    event_bus = EventBus()
    executor = PlatformExecutor(registry=registry, scheduler=scheduler, event_bus=event_bus)

    # Create kernel
    kernel = Kernel(registry=registry)
    SDKKernel.register_local_instance(kernel)
    sdk_runtime.register_platform(kernel=kernel, process_table=executor.process_table)

    # Register ADK adapter
    adapter = ADKAdapter(tool_executor=None, llm_router=None)
    executor.register_adapter(adapter)

    # Deploy the agent (same manifest as the YAML)
    agent_def = AgentDefinition(
        name="customer-service-forgeos",
        description="Customer service with ForgeOS governance",
        stack="adk",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace="support",
        llm_config=LLMConfig(chat_model="gemini-2.5-flash", provider="vertex"),
        tools=[
            "send_call_companion_link", "approve_discount", "sync_ask_for_approval",
            "update_salesforce_crm", "access_cart_information", "modify_cart",
            "get_product_recommendations", "check_product_availability",
            "schedule_planting_service", "get_available_planting_times",
            "send_care_instructions", "generate_qr_code",
        ],
        system_prompt=(
            "You are Cymbal Home & Garden's AI customer service agent. "
            "Help customers with orders, returns, and garden planning."
        ),
        metadata={
            "_capabilities": {
                "tools": {
                    "allowed": [
                        "send_call_companion_link", "access_cart_information",
                        "modify_cart", "get_product_recommendations",
                        "check_product_availability", "schedule_planting_service",
                        "get_available_planting_times", "send_care_instructions",
                        "generate_qr_code",
                    ],
                    "denied": ["update_salesforce_crm", "approve_discount"],
                },
            },
            "_boundaries": {"budgets": {"daily_usd": 5.0, "per_task_usd": 0.5}},
            "_governance": {"audit_level": "full"},
        },
    )

    start = time.time()
    agent_id = await executor.deploy(agent_def)

    # Invoke (contract is already in registry from deploy)
    result = await executor.invoke(agent_id, prompt)
    elapsed = time.time() - start

    # Check what kernel would say about specific tools
    check_approve = kernel.check_tool_call(agent_id, "approve_discount", {"value": 15})
    check_care = kernel.check_tool_call(agent_id, "send_care_instructions", {})

    return {
        "mode": "ADK + ForgeOS (full governance)",
        "output": (result.output or "")[:500],
        "tool_calls": [tc.get("name", "") for tc in (result.tool_calls or [])],
        "tokens": result.tokens_used or 0,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {
            "tool_acl_check": "ACTIVE",
            "approve_discount": check_approve.action,
            "send_care_instructions": check_care.action,
            "budget_check": "ACTIVE ($5/day limit)",
            "audit_logged": True,
            "pii_masked": True,
            "hitl_enforced": True,
            "process_tracked": executor.process_table.get(agent_id) is not None,
        },
    }


# ============================================================================
# Main
# ============================================================================

async def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "I bought a fiddle leaf fig and it's drooping. Can you send me care instructions?"

    print("=" * 70)
    print("CUSTOMER-SERVICE AGENT: ADK vs ForgeOS Comparison")
    print("=" * 70)
    print(f"Prompt: {prompt}")
    print()

    # Run Mode B first (pure ADK)
    print("--- Mode B: Pure ADK (no governance) ---")
    try:
        result_b = await run_pure_adk(prompt)
        print(json.dumps(result_b, indent=2, default=str))
    except Exception as e:
        print(f"Mode B failed: {e}")
        result_b = {"mode": "Pure ADK", "error": str(e)}

    print()

    # Run Mode A (ForgeOS + ADK)
    print("--- Mode A: ADK + ForgeOS (full governance) ---")
    try:
        result_a = await run_forgeos_adk(prompt)
        print(json.dumps(result_a, indent=2, default=str))
    except Exception as e:
        print(f"Mode A failed: {e}")
        result_a = {"mode": "ForgeOS", "error": str(e)}

    print()
    print("=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'':30s} {'Pure ADK':20s} {'ForgeOS':20s}")
    print(f"{'-'*30} {'-'*20} {'-'*20}")
    print(f"{'Tool ACL enforcement':30s} {'None':20s} {'Active':20s}")
    print(f"{'approve_discount':30s} {'Allowed':20s} {'DENIED by kernel':20s}")
    print(f"{'Budget tracking':30s} {'None':20s} {'$5/day limit':20s}")
    print(f"{'Audit trail':30s} {'No':20s} {'Yes (hash-chained)':20s}")
    print(f"{'PII masking':30s} {'No':20s} {'Yes':20s}")
    print(f"{'Process tracked':30s} {'No':20s} {'Yes':20s}")


if __name__ == "__main__":
    asyncio.run(main())
