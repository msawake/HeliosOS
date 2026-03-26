"""
ForgeOS Platform Bootstrap.

Multi-stack agent platform that supports four agent stacks (ForgeOS native,
CrewAI, Google ADK, OpenClaw) with five execution types (always-on, scheduled,
event-driven, reflex, autonomous) and personal/shared ownership.

Usage:
    python -m src.bootstrap [--company leadforge] [--config path/to/config.yaml] [--mode shadow|supervised|autonomous] [--dashboard] [--loop]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from stacks.base import AgentDefinition, ExecutionType, LLMConfig, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.crewai.adapter import CrewAIAdapter
from stacks.adk.adapter import ADKAdapter
from stacks.openclaw.adapter import OpenClawAdapter

from src.platform.registry import AgentRegistry
from src.platform.executor import PlatformExecutor
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import EventBus
from src.platform.llm_router import LLMRouter

from src.config.agent_configs import load_company_config, load_company_module, load_company_demo
from src.core.claude_client import ClaudeClient
from src.core.database import create_database_client
from src.core.model_client import create_llm_client, get_provider
from src.core.hooks import create_hook_chain
from src.mcp.custom_tools import CompanySystem
from src.mcp.server_manager import MCPServerManager
from src.mcp.tool_executor import ToolExecutor
from src.workflows.definitions import WorkflowEngine, WorkflowStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrap")


class OperatingMode:
    SHADOW = "shadow"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class PlatformBootstrap:
    """
    Boots the ForgeOS multi-stack platform. Initializes all four stack adapters,
    the platform executor, scheduler, event bus, and legacy company subsystems.
    """

    def __init__(
        self,
        config_path: str | None = None,
        mode: str = OperatingMode.SUPERVISED,
        company_id: str = "leadforge",
        tenant_id: str | None = None,
    ):
        self.company_id = company_id
        self.tenant_id = tenant_id or company_id
        self.config = load_company_config(config_path, company_id=company_id)
        self.mode = mode

        self.platform_registry = AgentRegistry()
        self.scheduler = SchedulerEngine()
        self.event_bus = EventBus()
        self.llm_router: LLMRouter | None = None
        self.executor: PlatformExecutor | None = None

        self.legacy_registry = None
        self.legacy_invoker = None
        self.system = None
        self.workflow_engine = None
        self._db = None
        self._running = False

    async def boot(self):
        logger.info("=" * 60)
        logger.info("BOOTING FORGEOS MULTI-STACK PLATFORM")
        logger.info("Company: %s | Mode: %s", self.company_id, self.mode)
        logger.info("Time: %s", datetime.now(timezone.utc).isoformat())
        logger.info("=" * 60)

        logger.info("[Phase 1] Initializing platform subsystems...")
        await self._init_platform()

        logger.info("[Phase 2] Initializing legacy company subsystems...")
        await self._init_legacy_subsystems()

        logger.info("[Phase 3] Registering stack adapters...")
        self._register_adapters()

        logger.info("[Phase 4] Building platform executor...")
        self.executor = PlatformExecutor(
            registry=self.platform_registry,
            scheduler=self.scheduler,
            event_bus=self.event_bus,
        )
        for name, adapter in self._adapters.items():
            self.executor.register_adapter(adapter)

        logger.info("[Phase 5] Seeding knowledge base...")
        if self.system:
            self.system.seed_knowledge_base(company_id=self.company_id)

        self._seed_dev_hitl_if_enabled()

        logger.info("[Phase 6] Creating workflow engine...")
        self.workflow_engine = WorkflowEngine(invoker=self.legacy_invoker)

        logger.info("[Phase 7] Starting scheduler...")
        self.scheduler.start_all()

        logger.info("=" * 60)
        logger.info("FORGEOS PLATFORM ONLINE")
        logger.info("Stacks: %s", list(self._adapters.keys()))
        logger.info("Platform agents: %d", len(self.platform_registry.list_all()))
        if self.legacy_registry:
            logger.info("Legacy agents: %d", len(self.legacy_registry.all_agents()))
        logger.info("Mode: %s", self.mode)
        logger.info("Dashboard: http://localhost:3000 (Next.js)")
        logger.info("API: http://localhost:5000 (Flask)")
        logger.info("=" * 60)

        self._running = True

    async def _init_platform(self):
        api_keys = {}
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            api_keys["anthropic"] = anthropic_key
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            api_keys["openai"] = openai_key

        self.llm_router = LLMRouter(api_keys=api_keys)
        logger.info("  LLM Router: providers=%s", self.llm_router.available_providers())

    def _seed_dev_hitl_if_enabled(self) -> None:
        """Seed sample pending approvals for local dashboard testing.

        Disable with FORGEOS_SEED_HITL=0|false|no.
        Skips if two or more items are already pending (idempotent in one process).
        """
        flag = os.environ.get("FORGEOS_SEED_HITL", "1").lower()
        if flag in ("0", "false", "no"):
            return
        if not self.system:
            return
        hitl = self.system.hitl
        if len(hitl.get_pending()) >= 2:
            return
        hitl.request_approval(
            requesting_agent="sales-ae",
            department="sales",
            category="outreach_compliance",
            title="Approve outbound sequence to TechCorp",
            description="Three-step email plus LinkedIn touch; 40 contacts in target list.",
            risk_assessment="medium",
            context={"deal_id": "demo-001", "sequence": "techcorp-q1"},
        )
        hitl.request_approval(
            requesting_agent="fin-ar",
            department="finance",
            category="financial",
            title="Waive late fee for renewal partner",
            description="Strategic account DataFlow Inc — one-time goodwill adjustment.",
            risk_assessment="low",
            context={"invoice_id": "demo-inv-99"},
        )
        logger.info("Seeded 2 demo HITL approvals (disable: FORGEOS_SEED_HITL=0)")

    async def _init_legacy_subsystems(self):
        """Initialize the existing ForgeOS company subsystems for backward compat."""
        self._db = create_database_client()
        if self._db.is_connected:
            logger.info("  Database: CONNECTED (PostgreSQL)")
        else:
            logger.info("  Database: IN-MEMORY (set DATABASE_URL for persistence)")

        self.system = CompanySystem(
            config=self.config, company_id=self.company_id, db_client=self._db,
        )
        redis_url = os.environ.get("REDIS_URL", "")
        hook_chain = create_hook_chain(
            config=self.config, hitl_gateway=self.system.hitl, redis_url=redis_url,
        )

        self._mcp_manager = MCPServerManager(self.config)
        mcp_clients = await self._mcp_manager.connect_all()

        tool_executor = ToolExecutor(company_system=self.system, mcp_clients=mcp_clients)
        for server_name, schemas in self._mcp_manager.get_all_tool_schemas().items():
            tool_executor.register_mcp_tools(server_name, schemas)

        default_model = self.config.get("models", {}).get("orchestrator_default", "claude-opus-4-6")
        llm_client = create_llm_client(default_model)

        claude_client = ClaudeClient(
            tool_executor=tool_executor, hook_chain=hook_chain, llm_client=llm_client,
        )

        company_mod = load_company_module(self.company_id)
        self.legacy_registry = company_mod.build_registry(
            company_name=self.config.get("company", {}).get("name", "Digital AI Corp")
        )
        from src.core.agent_invoker import AgentInvoker
        self.legacy_invoker = AgentInvoker(
            registry=self.legacy_registry,
            hook_chain=hook_chain,
            config=self.config,
            tool_executor=tool_executor,
            claude_client=claude_client,
        )

        self._tool_executor = tool_executor

    def _register_adapters(self):
        self._adapters = {
            "forgeos": ForgeOSAdapter(llm_router=self.llm_router, tool_executor=self._tool_executor),
            "crewai": CrewAIAdapter(llm_router=self.llm_router),
            "adk": ADKAdapter(llm_router=self.llm_router),
            "openclaw": OpenClawAdapter(llm_router=self.llm_router),
        }
        for name, adapter in self._adapters.items():
            logger.info("  Stack registered: %s", name)

    async def deploy_agent(self, agent_def: AgentDefinition) -> str:
        """Deploy an agent through the platform executor."""
        if not self.executor:
            raise RuntimeError("Platform not booted yet")
        return await self.executor.deploy(agent_def)

    async def run_main_loop(self, tick_interval: float = 30.0):
        logger.info("Starting main loop (tick every %.0fs)...", tick_interval)
        tick_count = 0
        while self._running:
            tick_count += 1
            try:
                dispatches = await self.workflow_engine.tick()
                if dispatches:
                    logger.info("Tick %d: Dispatched %d tasks", tick_count, len(dispatches))

                if self.system:
                    self.system.metrics.record("system.tick_count", tick_count, "operations")
                    self.system.metrics.record(
                        "system.active_workflows",
                        len(self.workflow_engine.list_workflows(WorkflowStatus.RUNNING)),
                        "operations",
                    )

                    expired = self.system.hitl.get_expired_pending()
                    for item in expired:
                        self.system.hitl.expire(item["id"])
                    if expired:
                        self.system.metrics.increment(
                            "hitl.expired_approvals", amount=len(expired), department="operations",
                        )

            except Exception as e:
                logger.error("Main loop error at tick %d: %s", tick_count, e)

            await asyncio.sleep(tick_interval)

    def stop(self):
        logger.info("Shutting down ForgeOS Platform...")
        self._running = False
        self.scheduler.stop_all()
        if self._db:
            self._db.close()

    def start_api_server(self, host: str = "0.0.0.0", port: int = 5000):
        """Start the Flask API server (JSON only, no frontend)."""
        from src.dashboard.app import create_app
        company_name = self.config.get("company", {}).get("name", "AI Company")
        app = create_app(
            company_system=self.system,
            workflow_engine=self.workflow_engine,
            company_name=company_name,
            platform_executor=self.executor,
            platform_registry=self.platform_registry,
        )
        if app:
            logger.info("API server starting on http://%s:%d", host, port)
            import threading
            thread = threading.Thread(
                target=lambda: app.run(host=host, port=port, debug=False),
                daemon=True,
            )
            thread.start()
        else:
            logger.warning("API server not available (Flask not installed)")

    def get_platform_summary(self) -> dict:
        return {
            "company_id": self.company_id,
            "mode": self.mode,
            "stacks": list(self._adapters.keys()) if hasattr(self, "_adapters") else [],
            "platform_registry": self.platform_registry.summary(),
            "scheduler_jobs": self.scheduler.list_jobs(),
            "event_subscriptions": self.event_bus.get_subscriptions(),
        }


CompanyBootstrap = PlatformBootstrap


def run_demo(company_id: str = "leadforge"):
    demo_mod = load_company_demo(company_id)
    demo_mod.run_demo()


async def main():
    parser = argparse.ArgumentParser(description="Boot ForgeOS Multi-Stack Platform")
    parser.add_argument("--company", type=str, default="leadforge", help="Company ID")
    parser.add_argument("--config", type=str, help="Path to company config YAML")
    parser.add_argument(
        "--mode",
        choices=["shadow", "supervised", "autonomous"],
        default="supervised",
    )
    parser.add_argument("--demo", action="store_true", help="Run demo scenario")
    parser.add_argument("--dashboard", action="store_true", help="Start API server")
    parser.add_argument("--loop", action="store_true", help="Run main operational loop")
    args = parser.parse_args()

    bootstrap = PlatformBootstrap(config_path=args.config, mode=args.mode, company_id=args.company)
    await bootstrap.boot()

    if args.demo:
        run_demo(company_id=args.company)

    if args.dashboard:
        bootstrap.start_api_server()

    if args.loop:
        try:
            await bootstrap.run_main_loop()
        except KeyboardInterrupt:
            bootstrap.stop()
    elif args.dashboard:
        logger.info("API server running on http://0.0.0.0:5000 — Ctrl+C to stop.")
        try:
            await asyncio.Future()
        except KeyboardInterrupt:
            pass
        finally:
            bootstrap.stop()


if __name__ == "__main__":
    asyncio.run(main())
