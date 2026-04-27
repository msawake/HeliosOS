#!/usr/bin/env python3
"""
Deploy all 50 platform agents across every stack and execution type.

Run from repo root:
  PYTHONPATH=. python scripts/deploy_all_agents.py

Each agent is constructed from the definitions in
``src.platform.agent_definitions`` and deployed via the PlatformExecutor.
Failures are logged per-agent so one bad definition does not block the rest.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.agent_definitions import ALL_AGENTS
from stacks.base import (
    AgentDefinition,
    ExecutionType,
    LLMConfig,
    OwnershipType,
)
from stacks.adk.adapter import ADKAdapter
from stacks.crewai.adapter import CrewAIAdapter
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.openclaw.adapter import OpenClawAdapter
from src.platform.event_bus import EventBus
from src.platform.executor import PlatformExecutor
from src.platform.llm_router import LLMRouter
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("deploy_all_agents")


def _build_agent_definition(spec: dict) -> AgentDefinition:
    """Construct an ``AgentDefinition`` from a raw dict specification."""
    llm_raw = spec.get("llm_config", {})
    llm_config = LLMConfig(
        chat_model=llm_raw.get("chat_model", "claude-sonnet-4-5-20250514"),
        reasoning_model=llm_raw.get("reasoning_model"),
        provider=llm_raw.get("provider", "anthropic"),
    )
    return AgentDefinition(
        name=spec["name"],
        stack=spec["stack"],
        execution_type=spec["execution_type"],
        ownership=spec.get("ownership", OwnershipType.SHARED),
        owner_id=spec.get("owner_id"),
        llm_config=llm_config,
        schedule=spec.get("schedule"),
        event_triggers=spec.get("event_triggers", []),
        goal=spec.get("goal"),
        tools=spec.get("tools", []),
        description=spec.get("description", ""),
        department=spec.get("department", ""),
        metadata=spec.get("metadata", {}),
        system_prompt=spec.get("system_prompt", ""),
    )


def _boot_platform() -> PlatformExecutor:
    """Initialise the platform with all stack adapters registered."""
    registry = AgentRegistry()
    scheduler = SchedulerEngine()
    event_bus = EventBus()
    llm = LLMRouter(api_keys={})

    executor = PlatformExecutor(
        registry=registry,
        scheduler=scheduler,
        event_bus=event_bus,
        agents_root=ROOT / "agents",
    )
    executor.register_adapter(ForgeOSAdapter(llm_router=llm))
    executor.register_adapter(CrewAIAdapter(llm_router=llm))
    executor.register_adapter(ADKAdapter(llm_router=llm))
    executor.register_adapter(OpenClawAdapter(llm_router=llm))

    return executor


async def deploy_all(executor: PlatformExecutor) -> tuple[list[dict], list[dict]]:
    """Deploy every agent in ALL_AGENTS.

    Returns ``(deployed, failed)`` where each element is a list of dicts
    with summary information about the agent.
    """
    deployed: list[dict] = []
    failed: list[dict] = []

    for spec in ALL_AGENTS:
        name = spec.get("name", "<unknown>")
        try:
            agent_def = _build_agent_definition(spec)
            agent_id = await executor.deploy(agent_def)
            deployed.append({
                "agent_id": agent_id,
                "name": name,
                "stack": agent_def.stack,
                "execution_type": agent_def.execution_type.value,
            })
            logger.info(
                "Deployed %-35s [stack=%-9s type=%-13s id=%s]",
                name, agent_def.stack, agent_def.execution_type.value, agent_id,
            )
        except Exception:
            logger.exception("Failed to deploy agent '%s'", name)
            failed.append({
                "name": name,
                "stack": spec.get("stack", "?"),
                "execution_type": getattr(
                    spec.get("execution_type"), "value", "?"
                ),
            })

    return deployed, failed


def _print_summary(deployed: list[dict], failed: list[dict]) -> None:
    """Print a human-readable deployment summary."""
    total = len(deployed) + len(failed)
    print("\n" + "=" * 70)
    print(f"  DEPLOYMENT SUMMARY  ({total} agents)")
    print("=" * 70)
    print(f"  Deployed : {len(deployed)}")
    print(f"  Failed   : {len(failed)}")
    print()

    # By stack
    stack_counts = Counter(a["stack"] for a in deployed)
    print("  By stack:")
    for stack in sorted(stack_counts):
        print(f"    {stack:<12} {stack_counts[stack]}")
    print()

    # By execution type
    type_counts = Counter(a["execution_type"] for a in deployed)
    print("  By execution type:")
    for etype in sorted(type_counts):
        print(f"    {etype:<16} {type_counts[etype]}")
    print()

    # Deployed table
    if deployed:
        print(f"  {'ID':<14} {'Name':<38} {'Stack':<12} {'Type'}")
        print("  " + "-" * 78)
        for a in deployed:
            print(
                f"  {a['agent_id']:<14} {a['name']:<38} {a['stack']:<12} {a['execution_type']}"
            )
        print()

    # Failed table
    if failed:
        print("  FAILED AGENTS:")
        print(f"  {'Name':<38} {'Stack':<12} {'Type'}")
        print("  " + "-" * 62)
        for a in failed:
            print(f"  {a['name']:<38} {a['stack']:<12} {a['execution_type']}")
        print()

    print("=" * 70)


async def main() -> None:
    logger.info("Booting platform...")
    executor = _boot_platform()

    logger.info("Deploying %d agents...", len(ALL_AGENTS))
    deployed, failed = await deploy_all(executor)

    # Stop background loops so the process exits cleanly
    logger.info("Stopping background tasks...")
    for a in deployed:
        await executor.stop_agent(a["agent_id"])

    _print_summary(deployed, failed)


if __name__ == "__main__":
    asyncio.run(main())
