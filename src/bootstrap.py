"""
LeadForge AI Bootstrap Script.

This is the "power switch" for LeadForge AI. It:
1. Loads company configuration
2. Builds the agent registry (all 26 agents)
3. Initializes the company subsystems (event bus, HITL, knowledge base, metrics)
4. Seeds the knowledge base with company policies
5. Creates the workflow engine
6. Boots the executive layer (CEO, COO, CFO)
7. Starts standing swarms (support, monitoring)
8. Launches the HITL dashboard
9. Begins the main operational loop

Usage:
    python -m src.bootstrap [--config path/to/config.yaml] [--mode shadow|supervised|autonomous]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config.agent_configs import build_registry, load_company_config
from src.core.agent_invoker import AgentInvoker, AgentTier, TaskMetadata, create_invoker
from src.core.hooks import create_hook_chain
from src.mcp.custom_tools import CompanySystem
from src.workflows.definitions import (
    WorkflowEngine,
    create_client_onboarding_workflow,
    create_compliance_audit_workflow,
    create_lead_qualification_workflow,
    create_lead_nurture_workflow,
    create_leadforge_sales_workflow,
    create_marketing_campaign_workflow,
    create_outbound_campaign_workflow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrap")


class OperatingMode:
    SHADOW = "shadow"          # Agents run but outputs go to review queue
    SUPERVISED = "supervised"  # Agents primary, human reviews before external actions
    AUTONOMOUS = "autonomous"  # Agents independent, humans review daily summaries


class CompanyBootstrap:
    """
    Bootstraps the entire AI company from zero.
    Call boot() to start everything.
    """

    def __init__(self, config_path: str | None = None, mode: str = OperatingMode.SUPERVISED):
        self.config = load_company_config(config_path)
        self.mode = mode
        self.registry = None
        self.invoker = None
        self.system = None
        self.workflow_engine = None
        self._running = False

    async def boot(self):
        """Main boot sequence. Starts the entire company."""
        logger.info("=" * 60)
        logger.info("BOOTING AI COMPANY")
        logger.info("Mode: %s", self.mode)
        logger.info("Time: %s", datetime.now(timezone.utc).isoformat())
        logger.info("=" * 60)

        # Phase 1: Initialize subsystems
        logger.info("[Phase 1] Initializing subsystems...")
        await self._init_subsystems()

        # Phase 2: Build agent registry
        logger.info("[Phase 2] Building agent registry (26 agents)...")
        await self._build_registry()

        # Phase 3: Seed knowledge base
        logger.info("[Phase 3] Seeding knowledge base...")
        self.system.seed_knowledge_base()

        # Phase 4: Create workflow engine
        logger.info("[Phase 4] Creating workflow engine...")
        self.workflow_engine = WorkflowEngine(invoker=self.invoker)

        # Phase 5: Boot executive layer
        logger.info("[Phase 5] Booting executive layer...")
        await self._boot_executives()

        # Phase 6: Start standing swarms
        logger.info("[Phase 6] Starting standing swarms...")
        await self._start_standing_swarms()

        # Phase 7: Record initial metrics
        logger.info("[Phase 7] Recording initial metrics...")
        self._record_boot_metrics()

        # Phase 8: System ready
        logger.info("=" * 60)
        logger.info("AI COMPANY ONLINE")
        logger.info("Agents registered: %d", len(self.registry.all_agents()))
        logger.info("Knowledge base entries: %d", len(self.system.knowledge._entries))
        logger.info("Mode: %s", self.mode)
        logger.info("Dashboard: http://localhost:5000")
        logger.info("=" * 60)

        self._running = True

    async def _init_subsystems(self):
        """Initialize all company subsystems."""
        self.system = CompanySystem()
        hook_chain = create_hook_chain(config=self.config)
        self.registry = build_registry(
            company_name=self.config.get("company", {}).get("name", "Digital AI Corp")
        )
        self.invoker = AgentInvoker(
            registry=self.registry,
            hook_chain=hook_chain,
            config=self.config,
        )

    async def _build_registry(self):
        """Registry is already built by build_registry(). Log summary."""
        agents = self.registry.all_agents()
        by_tier = {}
        by_dept = {}
        for a in agents:
            tier_name = a.tier.name
            by_tier.setdefault(tier_name, 0)
            by_tier[tier_name] += 1
            by_dept.setdefault(a.department, 0)
            by_dept[a.department] += 1

        logger.info("  Agents by tier: %s", dict(by_tier))
        logger.info("  Agents by dept: %s", dict(by_dept))

    async def _boot_executives(self):
        """Boot the three executive orchestrators."""
        executives = ["exec-ceo", "exec-coo", "exec-cfo"]

        for agent_id in executives:
            config = self.registry.get(agent_id)
            if config:
                logger.info("  Booted: %s (%s) [%s]", config.name, agent_id, config.model)

                # In production: create persistent ClaudeSDKClient session
                # client = ClaudeSDKClient(
                #     model=config.model,
                #     system_prompt=config.system_prompt,
                #     mcp_servers=config.mcp_servers,
                # )
                # Save session_id for resumption

    async def _start_standing_swarms(self):
        """Start always-on agent swarms."""
        standing_agents = [
            ("sales-sdr", "Sales SDR", 3),                  # 3 instances
            ("ops-monitoring", "System Monitoring", 1),     # 1 instance
        ]

        for agent_id, name, instances in standing_agents:
            config = self.registry.get(agent_id)
            if config:
                logger.info(
                    "  Standing swarm: %s x%d [%s]",
                    name, instances, config.model,
                )
                # In production: spawn N instances of each standing agent
                # Each instance runs as a Kubernetes deployment with autoscaling

    def _record_boot_metrics(self):
        """Record initial boot metrics."""
        self.system.metrics.record("system.boot_count", 1, "operations")
        self.system.metrics.record("agents.total", len(self.registry.all_agents()), "operations")
        self.system.metrics.record("system.mode", 1 if self.mode == "autonomous" else 0, "operations")

    async def run_main_loop(self, tick_interval: float = 30.0):
        """
        Main operational loop. Runs continuously.
        Each tick:
        1. Workflow engine processes ready tasks
        2. Department orchestrators poll for events
        3. HITL gateway checks for expired approvals
        4. Metrics are updated
        """
        logger.info("Starting main loop (tick every %.0fs)...", tick_interval)

        tick_count = 0
        while self._running:
            tick_count += 1

            try:
                # 1. Workflow engine tick
                dispatches = await self.workflow_engine.tick()
                if dispatches:
                    logger.info("Tick %d: Dispatched %d tasks", tick_count, len(dispatches))

                # 2. Record heartbeat
                self.system.metrics.record("system.tick_count", tick_count, "operations")
                self.system.metrics.record(
                    "system.active_workflows",
                    len(self.workflow_engine.list_workflows(WorkflowStatus.RUNNING)),
                    "operations",
                )

                # 3. Check for stale approvals
                pending = self.system.hitl.get_pending()
                if pending:
                    self.system.metrics.record(
                        "hitl.pending_approvals",
                        len(pending),
                        "operations",
                    )

            except Exception as e:
                logger.error("Main loop error at tick %d: %s", tick_count, e)
                self.system.metrics.increment("system.errors", department="operations")

            await asyncio.sleep(tick_interval)

    def stop(self):
        """Gracefully stop the company."""
        logger.info("Shutting down AI Company...")
        self._running = False

    def start_dashboard(self, host: str = "0.0.0.0", port: int = 5000):
        """Start the HITL dashboard web server."""
        from src.dashboard.app import create_app
        app = create_app(
            company_system=self.system,
            workflow_engine=self.workflow_engine,
        )
        if app:
            logger.info("Dashboard starting on http://%s:%d", host, port)
            # Run in a separate thread so it does not block the main loop
            import threading
            thread = threading.Thread(
                target=lambda: app.run(host=host, port=port, debug=False),
                daemon=True,
            )
            thread.start()
        else:
            logger.warning("Dashboard not available (Flask not installed)")


# ---------------------------------------------------------------------------
# Demo: run a sample workflow
# ---------------------------------------------------------------------------

def run_demo():
    """Run a demo showcasing LeadForge AI capabilities."""
    from src.config.agent_configs import build_registry
    from src.mcp.custom_tools import CompanySystem

    print("\n" + "=" * 70)
    print("  LeadForge AI — Demo Mode")
    print("  AI-Powered B2B Lead Generation Agency")
    print("=" * 70)

    system = CompanySystem()
    system.seed_knowledge_base()

    # Demo 1: Lead Qualification Workflow
    print("\n📋 Demo 1: Lead Qualification Workflow")
    print("-" * 50)
    wf = create_lead_qualification_workflow(
        prospect_name="Sarah Chen",
        prospect_email="sarah.chen@techcorp.com",
        prospect_company="TechCorp",
        client_name="Acme SaaS",
        source="inbound",
    )
    print(f"  Created workflow: {wf.name}")
    print(f"  Tasks: {len(wf.tasks)}")
    ready = wf.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready]}")

    # Demo 2: Client Onboarding Workflow
    print("\n📋 Demo 2: Client Onboarding Workflow")
    print("-" * 50)
    wf2 = create_client_onboarding_workflow(
        client_name="Acme SaaS",
        client_contact_email="cto@acmesaas.com",
        retainer_amount_usd=5000,
        services=["outbound email", "LinkedIn outreach", "lead scoring"],
    )
    print(f"  Created workflow: {wf2.name}")
    print(f"  Tasks: {len(wf2.tasks)}")
    ready2 = wf2.get_ready_tasks()
    print(f"  Ready to execute: {[t.name for t in ready2]}")

    # Demo 3: HITL Approval Request
    print("\n📋 Demo 3: HITL Approval — Google Ads Budget Increase")
    print("-" * 50)
    req_id = system.hitl.request_approval(
        requesting_agent="mkt-ppc",
        department="marketing",
        category="ad_spend",
        title="Increase Google Ads daily budget from $200 to $350",
        description="Campaign 'B2B Lead Gen — Non-Brand' showing strong ROAS of 4.2x. "
                    "Recommend increasing daily budget by 75% to capture more impression share. "
                    "Expected additional spend: $4,500/month.",
    )
    pending = system.hitl.get_pending()
    print(f"  Approval request created: {req_id[:8]}...")
    print(f"  Pending approvals: {len(pending)}")
    print(f"  Category: {pending[0]['category']}")

    # Demo 4: Cross-Department Event
    print("\n📋 Demo 4: Cross-Department Event — Sales → Marketing")
    print("-" * 50)
    event_id = system.event_bus.publish(
        source_agent="sales-lead",
        source_department="sales",
        target_department="marketing",
        event_type="REQUEST",
        category="CONTENT_REQUEST",
        payload={
            "client": "Acme SaaS",
            "request": "Need case study for enterprise SaaS prospects",
            "target_persona": "VP of Sales at B2B SaaS companies",
            "deadline": "2026-03-15",
        },
        priority="P2_MEDIUM",
    )
    events = system.event_bus.query(target_department="marketing")
    print(f"  Event published: {event_id[:8]}...")
    print(f"  Pending marketing events: {len(events)}")

    # Demo 5: Knowledge Base Query
    print("\n📋 Demo 5: Knowledge Base — Lead Scoring Criteria")
    print("-" * 50)
    results = system.knowledge.search("lead scoring qualification")
    print(f"  Found {len(results)} matching entries:")
    for r in results[:3]:
        print(f"    - {r['title']}")

    # Summary
    print("\n" + "=" * 70)
    print("  Demo Complete!")
    print(f"  System health: {system.get_system_health()['pending_events']} pending events")
    print(f"  Knowledge base: {len(system.knowledge._entries)} policies loaded")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Boot the AI Company")
    parser.add_argument("--config", type=str, help="Path to company config YAML")
    parser.add_argument(
        "--mode",
        choices=["shadow", "supervised", "autonomous"],
        default="supervised",
        help="Operating mode",
    )
    parser.add_argument("--demo", action="store_true", help="Run demo scenario")
    parser.add_argument("--dashboard", action="store_true", help="Start dashboard")
    parser.add_argument("--loop", action="store_true", help="Run main operational loop")
    args = parser.parse_args()

    bootstrap = CompanyBootstrap(config_path=args.config, mode=args.mode)
    await bootstrap.boot()

    if args.demo:
        run_demo()

    if args.dashboard:
        bootstrap.start_dashboard()

    if args.loop:
        try:
            await bootstrap.run_main_loop()
        except KeyboardInterrupt:
            bootstrap.stop()


if __name__ == "__main__":
    asyncio.run(main())
