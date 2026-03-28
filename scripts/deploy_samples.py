#!/usr/bin/env python3
"""
Deploy demo agents across all stacks and execution types.

Run from repo root:
  PYTHONPATH=. python scripts/deploy_samples.py

Scaffolds land under agents/shared/ and agents/personal/ (gitignored by default).
Background loops are stopped after deploy so the process exits cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stacks.adk.adapter import ADKAdapter
from stacks.base import (
    AgentDefinition,
    ExecutionType,
    LLMConfig,
    OwnershipType,
)
from stacks.crewai.adapter import CrewAIAdapter
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.openclaw.adapter import OpenClawAdapter
from src.platform.event_bus import EventBus
from src.platform.executor import PlatformExecutor
from src.platform.llm_router import LLMRouter
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("deploy_samples")


def _samples() -> list[AgentDefinition]:
    demo_owner = "demo-user"
    return [
        AgentDefinition(
            name="lead-qualifier",
            stack="forgeos",
            execution_type=ExecutionType.SCHEDULED,
            ownership=OwnershipType.SHARED,
            description="Score and prioritize inbound B2B leads.",
            schedule="every 1h",
            department="sales",
            llm_config=LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"),
        ),
        AgentDefinition(
            name="meeting-notes",
            stack="forgeos",
            execution_type=ExecutionType.EVENT_DRIVEN,
            ownership=OwnershipType.PERSONAL,
            owner_id=demo_owner,
            description="Summarize meetings and draft follow-ups.",
            event_triggers=["calendar.meeting_ended"],
            department="operations",
            llm_config=LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"),
        ),
        AgentDefinition(
            name="marketing-content-crew",
            stack="crewai",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            description="On-demand campaign copy and social snippets.",
            goal="Produce concise, on-brand marketing content.",
            tools=["web_search", "brand_voice_mcp"],
            department="marketing",
            llm_config=LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"),
        ),
        AgentDefinition(
            name="sales-research-crew",
            stack="crewai",
            execution_type=ExecutionType.AUTONOMOUS,
            ownership=OwnershipType.SHARED,
            description="Deep research loop for a named account.",
            goal="Produce a short prospect brief with triggers and talk tracks.",
            metadata={"max_iterations": 3, "loop_interval_seconds": 0.5},
            tools=["crm_lookup", "news_mcp"],
            department="sales",
            llm_config=LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"),
        ),
        AgentDefinition(
            name="compliance-auditor",
            stack="adk",
            execution_type=ExecutionType.SCHEDULED,
            ownership=OwnershipType.SHARED,
            description="Weekly enterprise policy and controls review assistant.",
            schedule="every 168h",
            department="legal",
            llm_config=LLMConfig(chat_model="gemini-2.0-flash", provider="google"),
        ),
        AgentDefinition(
            name="inbox-manager",
            stack="openclaw",
            execution_type=ExecutionType.ALWAYS_ON,
            ownership=OwnershipType.PERSONAL,
            owner_id=demo_owner,
            description="Triage inbox, label, draft replies; pause before send.",
            goal="Keep inbox at zero without unsafe sends.",
            event_triggers=["email.incoming"],
            tools=["mail_fetch", "mail_draft", "calendar_freebusy"],
            metadata={"heartbeat_interval_seconds": 3600},
            department="general",
            llm_config=LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"),
        ),
    ]


async def main() -> None:
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

    rows: list[tuple[str, str, str, str, Path]] = []

    for agent_def in _samples():
        aid = await executor.deploy(agent_def)
        cfg = registry.get(aid)
        agent_dir = Path(cfg.config_path) if cfg and cfg.config_path else Path()
        rows.append(
            (
                aid,
                agent_def.name,
                agent_def.stack,
                agent_def.execution_type.value,
                agent_dir,
            )
        )
        logger.info("Deployed %-12s [%s] -> %s", aid, agent_def.stack, agent_dir)

    scheduler.stop_all()
    for aid, *_ in rows:
        await executor.stop_agent(aid)

    print("\nSample agents scaffolded (background tasks stopped):\n")
    print(f"{'ID':<14} {'Name':<26} {'Stack':<10} {'Exec':<14} {'Path'}")
    print("-" * 100)
    for aid, name, stack, ex, p in rows:
        print(f"{aid:<14} {name:<26} {stack:<10} {ex:<14} {p}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
