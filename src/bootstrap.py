"""
Helios OS Platform Bootstrap.

Multi-stack agent platform that supports four agent stacks (Helios OS native,
CrewAI, Google ADK, OpenClaw) with five execution types (always-on, scheduled,
event-driven, reflex, autonomous) and personal/shared ownership.

Usage:
    python -m src.bootstrap [--company leadforge] [--config path/to/config.yaml] [--mode shadow|supervised|autonomous] [--dashboard] [--loop] [--port N]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from stacks.base import AgentDefinition
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.crewai.adapter import CrewAIAdapter
from stacks.adk.adapter import ADKAdapter
from stacks.openclaw.adapter import OpenClawAdapter
from stacks.sandbox.adapter import SandboxAdapter

from src.platform.registry import AgentRegistry
from src.platform.executor import PlatformExecutor
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import EventBus
from src.platform.llm_router import LLMRouter

from src.config.agent_configs import load_company_config, load_company_module, load_company_demo
from src.core.database import create_database_client
from src.core.model_client import ModelProvider, create_llm_client, get_provider

# Legacy imports — kept for backward compat, will be removed
try:
    from src.core.claude_client import ClaudeClient
    from src.core.hooks import create_hook_chain
except ImportError:
    ClaudeClient = None
    create_hook_chain = None
from forgeos_mcp.integration.custom_tools import CompanySystem
from forgeos_mcp.integration.server_manager import MCPServerManager
from forgeos_mcp.integration.tool_executor import ToolExecutor
from src.workflows.definitions import WorkflowEngine, WorkflowStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bootstrap")

# FORGEOS_KERNEL_VERBOSE: narrate every kernel admission decision live. The
# kernel emits these at INFO under src.platform.kernel.*; ensure that logger is
# at INFO regardless of any later level tweaks so the operator can watch it.
if os.environ.get("FORGEOS_KERNEL_VERBOSE", "0").strip().lower() in ("1", "true", "yes", "on"):
    logging.getLogger("src.platform.kernel").setLevel(logging.INFO)
    logger.info("FORGEOS_KERNEL_VERBOSE=1 — kernel admission decisions will be logged")


def _repo_root() -> Path:
    """Directory containing `pyproject.toml` (parent of `src/`)."""
    return Path(__file__).resolve().parent.parent


def _load_dotenv_from_repo_root() -> None:
    """Load `.env` from repo root first, then default search (cwd / parents).

    Requires `python-dotenv` (core dependency). If import fails, falls back
    to a safe manual parser that validates key names and skips empty values.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning(
            "python-dotenv not installed — falling back to safe manual .env parsing. "
            "Fix: pip install python-dotenv  (or reinstall this package)"
        )
        _load_dotenv_manual()
        return
    env_file = _repo_root() / ".env"
    if env_file.is_file():
        load_dotenv(env_file)
    load_dotenv()


def _load_dotenv_manual() -> None:
    """Safe fallback .env parser when python-dotenv is not available.

    Validates key names, skips empty values, and does not override
    existing environment variables.
    """
    env_path = _repo_root() / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            # Validate key name: must be a valid env var name
            if not key or not key.replace("_", "").replace("-", "").isalnum():
                continue
            # Skip empty values
            if not val:
                continue
            # Don't override existing env vars
            if key not in os.environ:
                os.environ[key] = val


class OperatingMode:
    SHADOW = "shadow"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class PlatformBootstrap:
    """
    Boots the Helios OS multi-stack platform. Initializes all four stack adapters,
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

        self.platform_registry = None  # initialized during boot
        self.scheduler = None
        self.event_bus = None
        self.llm_router: LLMRouter | None = None
        self.executor: PlatformExecutor | None = None
        self._runtime_service = None  # runtime-v2 worker tier (FORGEOS_RUNTIME_WORKERS)

        self.legacy_registry = None
        self.legacy_invoker = None
        self.system = None
        self.workflow_engine = None
        self._db = None
        self._running = False
        self._init_stages: set[str] = set()  # tracks completed init stages for safe cleanup

    async def boot(self, api_listen_port: int = 5000):
        _load_dotenv_from_repo_root()
        logger.info("=" * 60)
        logger.info("BOOTING HELIOS OS MULTI-STACK PLATFORM")
        logger.info("Company: %s | Mode: %s", self.company_id, self.mode)
        logger.info("Time: %s", datetime.now(timezone.utc).isoformat())
        logger.info("=" * 60)

        try:
            logger.info("[Phase 1] Initializing platform subsystems...")
            await self._init_platform()

            logger.info("[Phase 2] Initializing legacy company subsystems...")
            await self._init_legacy_subsystems()

            # Platform persistence stores (backed by DB when available)
            agent_store = sub_store = job_store = msg_store = None
            if self._db and self._db.is_connected:
                try:
                    from src.platform.persistence import (
                        PostgresAgentRegistry,
                        PostgresEventSubscriptionStore,
                        PostgresScheduledJobStore,
                        PostgresAgentMessageStore,
                    )
                    agent_store = PostgresAgentRegistry(self._db, self.tenant_id)
                    sub_store = PostgresEventSubscriptionStore(self._db, self.tenant_id)
                    job_store = PostgresScheduledJobStore(self._db, self.tenant_id)
                    msg_store = PostgresAgentMessageStore(self._db, self.tenant_id)
                    logger.info("  Platform persistence: PostgreSQL")
                except Exception as exc:
                    logger.warning("  Platform persistence unavailable (%s) -- using in-memory", exc)

            self.platform_registry = AgentRegistry(store=agent_store)
            self.scheduler = SchedulerEngine(job_store=job_store)

            # Phase A #6 — durable event store. When FORGEOS_STATE_DIR is set,
            # the EventBus appends every fired event to a SQLite log so recent_events
            # survives restarts and multi-worker deploys stop silently fragmenting
            # history. Falls back to in-memory when the env var is absent.
            event_store = None
            state_dir = os.environ.get("FORGEOS_STATE_DIR")
            if state_dir:
                try:
                    from pathlib import Path
                    from src.platform.durable_event_store import SqliteEventStore
                    Path(state_dir).mkdir(parents=True, exist_ok=True)
                    event_store = SqliteEventStore(Path(state_dir) / "events.db")
                    logger.info("  Event bus: durable (SQLite at %s/events.db)", state_dir)
                except Exception as exc:
                    logger.warning(
                        "  Event bus durable store failed (%s) — falling back to in-memory", exc
                    )
            self.event_bus = EventBus(
                subscription_store=sub_store,
                message_store=msg_store,
                event_store=event_store,
            )

            # Phase A #5 — audit-aware secrets manager. Every secret read
            # (cache hit, secret-manager fetch, env fallback) now records
            # an audit row via the platform audit log when the FastAPI
            # layer wires it (via self._kernel.audit_log setter below).
            from src.core.secrets import SecretsManager
            # Encrypted Postgres backend for credentials (works locally without
            # GCP Secret Manager; also lets `secret:<name>` MCP env refs resolve
            # to per-user credentials). No-op when DB is absent.
            _secret_backend = None
            if getattr(self, "_db", None) is not None and self._db.is_connected:
                try:
                    from src.core.secret_backends import PostgresSecretBackend
                    _secret_backend = PostgresSecretBackend(self._db, tenant_id=self.tenant_id)
                    logger.info("  Secret backend: POSTGRES (encrypted)")
                except Exception as e:
                    logger.warning("  Postgres secret backend unavailable: %s", e)
            self.secrets = SecretsManager(
                audit_recorder=None,  # recorder bound after kernel
                db_backend=_secret_backend,
                cache_ttl=60,
            )
            from src.platform.credentials import CredentialStore
            self.credentials = CredentialStore(self.secrets, tenant_id=self.tenant_id)
            # The LLM router is built earlier (in _init_platform) than the
            # SecretsManager, so bind it now — this lets per-agent `api_key_ref`
            # values (e.g. `secret:litellm-allycode-key`) resolve at invoke time.
            if getattr(self, "llm_router", None) is not None:
                self.llm_router.bind_secrets(self.secrets)
            # The MCP managers were constructed in Phase 2 (_init_legacy_subsystems)
            # before self.secrets existed, so they captured secrets_manager=None and
            # could not resolve `secret:<name>` env refs (per-user MCP creds). Back-fill
            # now that the SecretsManager exists.
            for _mgr_attr in ("_client_mcp_manager", "_mcp_manager"):
                _mgr = getattr(self, _mgr_attr, None)
                if _mgr is not None and getattr(_mgr, "_secrets_manager", None) is None:
                    _mgr._secrets_manager = self.secrets

            # connect_all() ran in Phase 2 with secrets_manager=None, so any
            # config.yaml MCP server using `secret:` env refs came up with empty
            # creds (e.g. mcp-atlassian connects but discovers 0 tools). Now that
            # secrets resolve, reconnect those servers and re-register their tools.
            await self._reconnect_secret_ref_mcp_servers()

            logger.info("[Phase 3] Registering stack adapters...")
            self._register_adapters()

            logger.info("[Phase 4] Building platform executor...")
            # Initialize session store for multi-turn agent chat
            from src.core.session_store import InMemorySessionStore
            self._session_store = InMemorySessionStore()
            logger.info("  Session store: IN-MEMORY")

            # Use PostgreSQL process table if DATABASE_URL is set
            _process_table = None
            if hasattr(self, '_db') and self._db and self._db.is_connected:
                try:
                    from src.platform.postgres_process_table import PostgresProcessTable
                    _process_table = PostgresProcessTable(
                        db_pool=self._db._pool,
                        registry=self.platform_registry,
                    )
                    logger.info("  Process table: POSTGRESQL (durable)")
                except Exception as e:
                    logger.warning("  PostgreSQL process table failed: %s — using in-memory", e)

            self.executor = PlatformExecutor(
                registry=self.platform_registry,
                scheduler=self.scheduler,
                event_bus=self.event_bus,
                process_table=_process_table,
            )
            self.executor._session_store = self._session_store
            if getattr(self, 'credentials', None) is not None:
                self.executor.attach_credential_store(self.credentials)

            # Wire per-invocation history store when Postgres is available.
            if hasattr(self, '_db') and self._db and self._db.is_connected:
                try:
                    from src.platform.agent_runs_store import AgentRunsStore
                    self.executor.agent_runs = AgentRunsStore(self._db._pool)
                    logger.info("  Agent run history: POSTGRESQL")
                    # Reset any agent_runs left in 'running' from a prior
                    # process — they will never complete and would leak into
                    # /api/platform/agent-logs as ghost run.started events.
                    try:
                        n_swept = await self.executor.agent_runs.sweep_orphans()
                        if n_swept:
                            logger.info("  Reset %d orphan in-flight runs", n_swept)
                    except Exception as e:
                        logger.warning("  Orphan-run sweep failed: %s", e)
                except Exception as e:
                    logger.warning("  Agent run history disabled: %s", e)

            # Load persisted processes from PostgreSQL
            if _process_table and hasattr(_process_table, 'load_all'):
                try:
                    loaded = await _process_table.load_all()
                    if loaded:
                        logger.info("  Restored %d agent processes from PostgreSQL", loaded)
                except Exception as e:
                    logger.debug("  Process restore skipped: %s", e)
            # AgentOS: bind executor to A2A handler so agents can call each other
            if hasattr(self, "_a2a_handler") and self._a2a_handler:
                self._a2a_handler.bind_executor(self.executor)
                logger.info("  A2A handler: bound to platform executor")

            # License enforcement
            self._license_manager = None
            try:
                from src.billing.license import LicenseManager
                self._license_manager = LicenseManager(
                    db_client=self._db,
                    cache_ttl_seconds=int(os.environ.get("FORGEOS_LICENSE_CACHE_TTL", "300")),
                    grace_period_hours=int(os.environ.get("FORGEOS_LICENSE_GRACE_HOURS", "72")),
                )
                logger.info("  License manager: wired (cache_ttl=%ss)", os.environ.get("FORGEOS_LICENSE_CACHE_TTL", "300"))
            except Exception as e:
                logger.debug("  License manager initialization skipped: %s", e)

            # AgentOS: construct the Kernel facade + publish for in-process SDK use
            from src.platform.kernel import Kernel as PlatformKernel
            # Shared capability-token store. Capability tokens are minted on
            # approval (often in the API process) and validated on resume (often
            # in a separate worker process); with the default in-memory store the
            # worker can't see the token and HITL resume fails. A Postgres-backed
            # store makes tokens visible across processes. Falls back to in-memory
            # when there's no DB (single-process dev) or the community stub kernel.
            capability_store = None
            if self._db is not None and getattr(self._db, "is_connected", False):
                try:
                    from src.platform.kernel import PostgresCapabilityStore
                    capability_store = PostgresCapabilityStore(self._db)
                    logger.info("  Capability store: Postgres (shared across processes)")
                except ImportError:
                    logger.debug("  Capability store: PostgresCapabilityStore unavailable; in-memory")

            # Durable policy stores. In-memory policies vanish on restart /
            # scale-to-zero; the Postgres-backed stores survive and are visible
            # across the API and worker processes. The kernel holds the
            # namespace store (queried live per tool call) and a GlobalPolicy
            # value loaded once here; the global store is stashed on the kernel
            # so the policy-write API can persist edits.
            from src.platform.namespace_policy import NamespacePolicyStore
            namespace_policy_store = NamespacePolicyStore()
            global_policy = None
            global_policy_store = None
            if self._db is not None and getattr(self._db, "is_connected", False):
                from src.platform.namespace_policy import (
                    PostgresGlobalPolicyStore,
                    PostgresNamespacePolicyStore,
                )
                namespace_policy_store = PostgresNamespacePolicyStore(self._db, tenant_id=self.tenant_id)
                global_policy_store = PostgresGlobalPolicyStore(self._db, tenant_id=self.tenant_id)
                global_policy = global_policy_store.get()
                logger.info("  Policy stores: Postgres (namespace + global, durable)")
            else:
                logger.info("  Policy stores: in-memory (no DB — policies reset on restart)")

            self._kernel = PlatformKernel(
                registry=self.platform_registry,
                tool_executor=self._tool_executor,
                a2a_handler=getattr(self, '_a2a_handler', None),
                usage_enforcer=getattr(self, '_usage_enforcer', None),
                audit_log=None,  # wired by FastAPI layer which owns the AuditLog
                license_manager=self._license_manager,
                capability_store=capability_store,
                namespace_policy_store=namespace_policy_store,
                global_policy=global_policy,
            )
            # Stash the global-policy store so the policy-write API can persist
            # edits (the kernel itself only holds the GlobalPolicy value).
            self._kernel._global_policy_store = global_policy_store
            # Wire kernel into tool executor for mandatory policy enforcement
            self._tool_executor._kernel = self._kernel
            logger.info("  Kernel: wired into ToolExecutor (policy enforcement active)")
            # Let the kernel verify env.exec ownership via the EnvironmentManager.
            if getattr(self, "_environment_manager", None) is not None:
                try:
                    self._kernel.attach_environment_manager(self._environment_manager)
                except Exception:
                    logger.debug("could not attach environment manager to kernel", exc_info=True)

            # A2H Gateway — agent-to-human interaction
            self._a2h_gateway = None
            try:
                from src.platform.a2h import A2HGateway, HumanAgent
                self._a2h_gateway = A2HGateway(kernel=self._kernel)
                logger.info("  A2H Gateway: initialized")
                # Seed a default approver in the operations namespace so example
                # agents (jira-ticket-greeter-v2, etc.) that ask for
                # operations/approver work out of the box. Operators can
                # register additional humans via /api/a2h/humans.
                default_human = HumanAgent(
                    pid="operator-default",
                    name="approver",
                    namespace="operations",
                    role="Operator",
                    channels=["dashboard"],
                )
                self._a2h_gateway.register_human(default_human)
                logger.info("  A2H: seeded default human operations/approver")
            except Exception as e:
                logger.debug("  A2H Gateway initialization skipped: %s", e)

            # Wire A2H gateway into tool executor so agents can use human__* tools.
            # ToolExecutor.__init__ ran before A2H was constructed, so the
            # custom-handlers map is missing the human__* entries — rebuild it.
            if self._a2h_gateway:
                self._tool_executor._a2h_gateway = self._a2h_gateway
                self._tool_executor._custom_handlers = self._tool_executor._register_custom_tools()
                # Make the gateway visible to the executor too — used to
                # park agents in AWAITING_HUMAN after invoke leaves
                # outstanding A2H requests, and to auto-resume them on
                # approve/reject.
                self.executor._a2h_gateway = self._a2h_gateway
                logger.info("  A2H: wired into ToolExecutor (human__ask/notify/check available)")

            try:
                from src.forgeos_sdk.kernel import Kernel as SDKKernel
                SDKKernel.register_local_instance(self._kernel)
                logger.info("  Kernel: registered for in-process SDK access")
            except Exception as e:
                logger.debug("  SDK kernel registration skipped: %s", e)

            try:
                from src.forgeos_sdk.runtime import runtime as sdk_runtime
                sdk_runtime.register_platform(
                    kernel=self._kernel,
                    process_table=self.executor.process_table,
                    checkpoint_store=self.executor.checkpoint_store,
                    a2h_gateway=self._a2h_gateway,
                )
                logger.info("  Runtime: registered for in-process SDK access")
            except Exception as e:
                logger.debug("  SDK runtime registration skipped: %s", e)

            for name, adapter in self._adapters.items():
                self.executor.register_adapter(adapter)

            # Recover agents from persistent storage
            recovered = self.platform_registry.load_from_store()
            if recovered:
                await self.executor.recover()

            # Ensure the default tenant exists (required for FK constraints)
            if self._db and self._db.is_connected:
                try:
                    with self._db.admin() as conn:
                        existing = conn.execute_one(
                            "SELECT id FROM tenants WHERE id = %s", (self.tenant_id,)
                        )
                        if not existing:
                            conn.execute(
                                "INSERT INTO tenants (id, name, plan, status) "
                                "VALUES (%s, %s, 'starter', 'active')",
                                (self.tenant_id, self.config.get("company", {}).get("name", "Default")),
                            )
                            conn.commit()
                            logger.info("  Created default tenant: %s", self.tenant_id)
                        else:
                            logger.info("  Tenant %s already exists", self.tenant_id)

                        # Synthetic '_platform' client used as the namespace for
                        # platform-wide MCP server configurations managed via the
                        # Mission Control UI. Persisting under client_mcp_configs
                        # avoids duplicating the schema / RLS / CRUD layer.
                        existing_platform = conn.execute_one(
                            "SELECT id FROM clients WHERE id = %s", ("_platform",)
                        )
                        if not existing_platform:
                            conn.execute(
                                "INSERT INTO clients (id, tenant_id, name, status) "
                                "VALUES (%s, %s, 'Platform (system)', 'active')",
                                ("_platform", self.tenant_id),
                            )
                            conn.commit()
                            logger.info("  Created synthetic _platform client")
                except Exception as e:
                    logger.error(
                        "  Failed to ensure tenant '%s': %s. "
                        "Agent deployments may fail with FK constraint errors.",
                        self.tenant_id, e,
                    )

            logger.info("[Phase 5] Seeding knowledge base...")
            try:
                if self.system:
                    self.system.seed_knowledge_base(company_id=self.company_id)
                self._seed_dev_hitl_if_enabled()
            except Exception as e:
                logger.warning("  Seeding failed (non-fatal, continuing boot): %s", e)

            logger.info("[Phase 6] Creating workflow engine...")
            self.workflow_engine = WorkflowEngine(invoker=self.legacy_invoker)

            logger.info("[Phase 7] Starting scheduler...")
            self.scheduler.start_all()

        except Exception as e:
            logger.error("Boot failed at: %s", e)
            await self._cleanup()
            raise

        logger.info("=" * 60)
        logger.info("HELIOS OS PLATFORM ONLINE")
        logger.info("Stacks: %s", list(self._adapters.keys()))
        logger.info("Platform agents: %d", len(self.platform_registry.list_all()))
        if self.legacy_registry:
            logger.info("Legacy agents: %d", len(self.legacy_registry.all_agents()))
        logger.info("Mode: %s", self.mode)
        logger.info("Dashboard: http://localhost:3000 (Next.js)")
        logger.info("API: http://localhost:%d (FastAPI)", api_listen_port)
        logger.info("=" * 60)

        self._running = True

    async def _cleanup(self):
        """Clean up resources on boot failure. Only cleans stages that completed."""
        stages = getattr(self, '_init_stages', set())
        if "mcp_connected" in stages or "mcp_manager" in stages:
            if hasattr(self, '_mcp_manager') and self._mcp_manager:
                try:
                    await self._mcp_manager.disconnect_all()
                except Exception:
                    pass
        if "db_init" in stages:
            if hasattr(self, '_db') and self._db:
                try:
                    self._db.close()
                except Exception:
                    pass
        if hasattr(self, 'scheduler') and self.scheduler:
            try:
                self.scheduler.stop_all()
            except Exception:
                pass
        logger.info("Cleanup complete after boot failure (stages: %s)", stages)

    async def _init_platform(self):
        api_keys = {}
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            api_keys["anthropic"] = anthropic_key
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            api_keys["openai"] = openai_key
        atlas_key = os.environ.get("ATLAS_GATEWAY_KEY", "")
        if atlas_key:
            api_keys["atlas"] = atlas_key
        # vLLM / OpenAI-compatible gateway (e.g. the Qwen atlas-router). The router
        # builds an authenticated client when VLLM_API_KEY + VLLM_BASE_URL are set;
        # without the key it falls back to an "EMPTY" bearer and the gateway 401s.
        vllm_key = os.environ.get("VLLM_API_KEY", "")
        if vllm_key:
            api_keys["vllm"] = vllm_key
        gcp_project = os.environ.get("GCP_PROJECT_ID", "")
        if gcp_project:
            api_keys["vertex"] = gcp_project
        google_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        if google_key:
            api_keys["google"] = google_key

        self.llm_router = LLMRouter(api_keys=api_keys)
        logger.info("  LLM Router: providers=%s", self.llm_router.available_providers())

    def _seed_dev_hitl_if_enabled(self) -> None:
        """Seed sample pending approvals for local dashboard testing.

        Opt-in only: enable with FORGEOS_SEED_HITL=1|true|yes. Off by default so a
        normal boot doesn't surface phantom demo approvals in the dashboard.
        Skips if two or more items are already pending (idempotent in one process).
        """
        flag = os.environ.get("FORGEOS_SEED_HITL", "0").lower()
        if flag not in ("1", "true", "yes"):
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
        logger.info("Seeded 2 demo HITL approvals (FORGEOS_SEED_HITL=1)")

    async def _init_legacy_subsystems(self):
        """Initialize the existing Helios OS company subsystems for backward compat."""
        self._init_stages.add("db_init")
        self._db = create_database_client()
        if self._db.is_connected:
            logger.info("  Database: CONNECTED (PostgreSQL)")
            if os.environ.get("FORGEOS_SKIP_MIGRATIONS", "").lower() not in ("1", "true", "yes"):
                try:
                    from src.core.migrations import run_migrations
                    result = run_migrations(self._db)
                    logger.info(
                        "  Migrations: %d applied, %d skipped (of %d total)",
                        result.get("applied", 0),
                        result.get("skipped", 0),
                        result.get("total", 0),
                    )
                except Exception as e:
                    logger.error("  Migrations failed: %s", e)
                    raise
        else:
            logger.info("  Database: IN-MEMORY (set DATABASE_URL for persistence)")

        self.system = CompanySystem(
            config=self.config, company_id=self.company_id, db_client=self._db,
        )
        redis_url = os.environ.get("REDIS_URL", "")
        hook_chain = None
        if create_hook_chain:
            hook_chain = create_hook_chain(
                config=self.config, hitl_gateway=self.system.hitl, redis_url=redis_url,
            )

        self._mcp_manager = MCPServerManager(self.config, secrets_manager=getattr(self, 'secrets', None))
        self._init_stages.add("mcp_manager")
        try:
            mcp_clients = await asyncio.wait_for(
                self._mcp_manager.connect_all(),
                timeout=float(os.environ.get("FORGEOS_MCP_BOOT_TIMEOUT", "30")),
            )
            self._init_stages.add("mcp_connected")
        except asyncio.TimeoutError:
            logger.warning("MCP connect_all() timed out after 30s — continuing with partial MCP")
            mcp_clients = self._mcp_manager.get_clients()

        from forgeos_mcp.integration.client_mcp_manager import ClientMCPManager
        self._client_mcp_manager = ClientMCPManager(
            db_client=self._db,
            tenant_id=self.tenant_id,
            secrets_manager=getattr(self, 'secrets', None),
        )
        # AgentOS A2A handler (bound to platform_executor later in boot sequence)
        from src.platform.a2a import A2AHandler
        self._a2a_handler = A2AHandler()

        # Per-agent execution environments (k8s pods, kernel-gated env.exec).
        from src.platform.environments import EnvironmentManager
        self._environment_manager = EnvironmentManager(
            db_client=self._db, tenant_id=self.tenant_id,
        )

        # Reusable environment definitions (pod templates) + attach/detach service.
        from src.platform.env_service import EnvironmentService
        from src.platform.persistence import PostgresEnvDefStore
        self._env_def_store = PostgresEnvDefStore(self._db, self.tenant_id)
        self._env_service = EnvironmentService(
            env_def_store=self._env_def_store,
            registry=self.platform_registry,
            env_mgr=self._environment_manager,
        )

        tool_executor = ToolExecutor(
            company_system=self.system,
            mcp_clients=mcp_clients,
            client_mcp_manager=self._client_mcp_manager,
            a2a_handler=self._a2a_handler,
            environment_manager=self._environment_manager,
        )

        # Attach UsageEnforcer so the agentic loop can record tokens/cost.
        try:
            from src.billing.plans import UsageEnforcer
            self._usage_enforcer = UsageEnforcer(self._db)
            tool_executor._usage_enforcer = self._usage_enforcer
            logger.info("  Usage enforcer: wired to tool executor")
        except Exception as e:
            logger.warning("  Usage enforcer not wired: %s", e)
            self._usage_enforcer = None

        for server_name, schemas in self._mcp_manager.get_all_tool_schemas().items():
            tool_executor.register_mcp_tools(server_name, schemas)

        # Reconnect platform-scoped MCP servers persisted in Postgres. These are
        # the servers added at runtime via the dashboard (stored under the
        # synthetic "_platform" client); connecting them here makes those
        # registrations durable across reboots, just like config.yaml servers.
        await self._connect_persisted_platform_mcp(tool_executor)

        # Register platform-level tool stubs (CRM, HTTP, ads, MLS, etc.)
        try:
            from forgeos_mcp.integration.platform_tools import register_platform_tools
            register_platform_tools(tool_executor)
            logger.info("  Platform tools: registered")
        except ImportError:
            logger.debug("  Platform tools: not available (forgeos_mcp.integration.platform_tools missing)")

        default_model = self.config.get("models", {}).get("orchestrator_default", "claude-opus-4-6")
        try:
            orchestrator_provider = get_provider(default_model)
        except ValueError:
            orchestrator_provider = ModelProvider.ANTHROPIC
        if orchestrator_provider == ModelProvider.OPENAI:
            orchestrator_key = os.environ.get("OPENAI_API_KEY") or None
        else:
            orchestrator_key = os.environ.get("ANTHROPIC_API_KEY") or None
        llm_client = create_llm_client(default_model, api_key=orchestrator_key)

        # Legacy path: ClaudeClient + AgentInvoker (3-tier hierarchy).
        # Replaced by PlatformExecutor for new agents. Kept for backward
        # compat with company packages that use the legacy registry.
        try:
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
        except Exception as e:
            logger.warning("  Legacy invoker skipped: %s", e)
            self.legacy_registry = None
            self.legacy_invoker = None

        self._tool_executor = tool_executor

    def _register_adapters(self):
        te = self._tool_executor
        self._adapters = {
            "forgeos": ForgeOSAdapter(llm_router=self.llm_router, tool_executor=te),
            "crewai": CrewAIAdapter(llm_router=self.llm_router, tool_executor=te),
            "adk": ADKAdapter(llm_router=self.llm_router, tool_executor=te),
            "openclaw": OpenClawAdapter(
                llm_router=self.llm_router,
                tool_executor=te,
                openclaw_dir=os.environ.get("OPENCLAW_DIR", str(Path(__file__).resolve().parents[1] / "openclaw2")),
            ),
            "sandbox": SandboxAdapter(
                llm_router=self.llm_router,
                tool_executor=te,
                api_url=f"http://localhost:{os.environ.get('PORT', '5000')}",
            ),
        }
        for name, adapter in self._adapters.items():
            logger.info("  Stack registered: %s", name)

    async def deploy_agent(self, agent_def: AgentDefinition) -> str:
        """Deploy an agent through the platform executor."""
        if not self.executor:
            raise RuntimeError("Platform not booted yet")
        return await self.executor.deploy(agent_def)

    async def run_main_loop(self, tick_interval: float = 30.0, app=None):
        logger.info("Starting main loop (tick every %.0fs)...", tick_interval)
        tick_count = 0
        while self._running:
            tick_count += 1
            # Update liveness timestamp (used by /api/liveness probe)
            if app and hasattr(app, "state"):
                app.state.last_tick_at = datetime.now(timezone.utc)
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

    async def _reconnect_secret_ref_mcp_servers(self) -> None:
        """Reconnect config.yaml MCP servers whose env uses ``secret:`` refs.

        connect_all() runs before the SecretsManager exists, so a server with
        ``secret:<name>`` env vars first comes up with empty creds (e.g.
        mcp-atlassian connects but discovers 0 tools). Once secrets resolve we
        reconnect those servers, re-register their tools, and update the
        executor's client map so tool *calls* route to the live client.
        Best-effort: failures are logged and skipped.
        """
        mgr = getattr(self, "_mcp_manager", None)
        te = getattr(self, "_tool_executor", None)
        if mgr is None or te is None:
            return
        for cfg in mgr.get_server_configs():
            env = cfg.env_vars or {}
            if not any(isinstance(v, str) and v.startswith("secret:") for v in env.values()):
                continue
            # Already discovered tools at boot? Then its creds resolved; skip.
            if mgr.get_all_tool_schemas().get(cfg.name):
                continue
            try:
                schemas = await mgr.connect_one(cfg.name, cfg.package, env, cfg.args)
                te.register_mcp_tools(cfg.name, schemas)
                client = mgr.get_clients().get(cfg.name)
                if client is not None:
                    te._mcp_clients[cfg.name] = client
                if schemas:
                    logger.info("  Reconnected MCP '%s' with resolved secrets (%d tools)", cfg.name, len(schemas))
                else:
                    logger.warning("  MCP '%s' still 0 tools after secret resolution (check credentials)", cfg.name)
            except Exception as e:
                logger.warning("  Reconnect of MCP '%s' failed: %s", cfg.name, e)

    async def _connect_persisted_platform_mcp(self, tool_executor) -> None:
        """Connect platform MCP servers persisted in Postgres at boot.

        Reads the ``_platform`` client's ``client_mcp_configs`` rows (the
        servers registered at runtime via the dashboard) and brings each up
        through the same MCPServerManager used for ``config.yaml`` servers.
        Servers already declared in ``config.yaml`` win and are skipped — that
        file remains the canonical declarative source; the DB holds additions.

        Best-effort: a failed server is logged and skipped so one bad row never
        blocks boot. No-op when there's no DB.
        """
        if not self._db:
            return
        try:
            from src.platform.client_store import PostgresClientMCPStore
            store = PostgresClientMCPStore(db_client=self._db, tenant_id=self.tenant_id)
            persisted = store.list_for_client("_platform")
        except Exception as e:
            logger.warning("  Persisted platform MCP: could not read store: %s", e)
            return

        declared = {c.name for c in self._mcp_manager.get_server_configs()}
        connected = 0
        for cfg in persisted:
            name = cfg.get("server_name")
            if not name or not cfg.get("enabled", True):
                continue
            if name in declared:
                logger.debug("  Persisted platform MCP: '%s' already in config.yaml — skipping", name)
                continue
            try:
                schemas = await self._mcp_manager.connect_one(
                    name, cfg.get("package", ""), cfg.get("env_vars", {}), cfg.get("args", []),
                )
                tool_executor.register_mcp_tools(name, schemas)
                client = self._mcp_manager.get_clients().get(name)
                if client is not None:
                    tool_executor._mcp_clients[name] = client
                connected += 1
                if not schemas:
                    logger.warning(
                        "  Persisted platform MCP: '%s' connected but discovered 0 tools "
                        "(check credentials/env vars)", name,
                    )
            except Exception as e:
                logger.warning("  Persisted platform MCP: '%s' failed to connect: %s", name, e)
        if connected:
            logger.info("  Persisted platform MCP: reconnected %d server(s) from Postgres", connected)

    async def shutdown(self):
        """Graceful async shutdown — cancel tasks, disconnect services, close DB."""
        logger.info("Shutting down Helios OS Platform (graceful)...")
        self._running = False
        # 1. Cancel autonomous agent tasks
        if self.executor:
            for tid, task in list(getattr(self.executor, '_autonomous_tasks', {}).items()):
                task.cancel()
                logger.info("  Cancelled autonomous task: %s", tid)
        # 2. Stop scheduler
        if self.scheduler:
            self.scheduler.stop_all()
            logger.info("  Scheduler stopped")
        # 3. Disconnect MCP servers
        if hasattr(self, '_mcp_manager') and self._mcp_manager:
            try:
                await self._mcp_manager.disconnect_all()
                logger.info("  MCP servers disconnected")
            except Exception as e:
                logger.warning("  MCP disconnect error: %s", e)
        if hasattr(self, '_client_mcp_manager') and self._client_mcp_manager:
            try:
                await self._client_mcp_manager.disconnect_all()
            except Exception:
                pass
        # 4. Close database
        if self._db:
            try:
                self._db.close()
                logger.info("  Database closed")
            except Exception:
                pass
        logger.info("Shutdown complete.")

    def stop(self):
        """Synchronous stop — for backward compat and non-async contexts."""
        logger.info("Shutting down Helios OS Platform...")
        self._running = False
        if self.scheduler:
            self.scheduler.stop_all()
        if self._db:
            self._db.close()

    def _maybe_build_runtime_service(self):
        """Build + bind the runtime-v2 worker tier when FORGEOS_RUNTIME_WORKERS
        is set: a Redis-Streams (or in-memory) runnable queue + a stateless
        worker pool + resume service. Invokes then ENQUEUE a continuation and
        the workers drive it (no inline execution). Returns the service or None."""
        import os
        if self._runtime_service is not None:
            return self._runtime_service
        flag = os.environ.get("FORGEOS_RUNTIME_WORKERS", "").lower()
        if flag not in ("1", "true", "yes", "on"):
            return None
        try:
            from src.runtime import RuntimeService
            adapter = self.executor.get_adapter("forgeos") if self.executor else None
            kernel = getattr(self, "_kernel", None)
            if adapter is None or self.llm_router is None or kernel is None:
                logger.warning("runtime workers: missing deps (adapter/llm/kernel) — not starting")
                return None
            if self._db and getattr(self._db, "is_connected", False):
                from src.runtime import PostgresContinuationStore
                store = PostgresContinuationStore(self._db)
            else:
                from src.runtime import MemoryContinuationStore
                store = MemoryContinuationStore()
            svc = RuntimeService(
                kernel=kernel, llm_router=self.llm_router,
                tool_executor=self._tool_executor, registry=self.platform_registry,
                store=store, db=self._db,
            )
            # Share the engine so the adapter enqueues into the same store the
            # API reads (GET /runs, approvals) and the workers drive.
            adapter._step_engine = svc.engine
            adapter._runtime_service = svc
            self._runtime_service = svc
            logger.info("runtime workers: wired (queue + worker pool + resume service)")
            return svc
        except Exception:
            logger.exception("runtime workers: build failed — falling back to inline execution")
            return None

    def create_api_app(self, auth_enabled: bool = True):
        """Create the FastAPI app."""
        from src.dashboard.fastapi_app import create_fastapi_app
        company_name = self.config.get("company", {}).get("name", "AI Company")
        runtime_service = self._maybe_build_runtime_service()
        return create_fastapi_app(
            runtime_service=runtime_service,
            company_system=self.system,
            workflow_engine=self.workflow_engine,
            company_name=company_name,
            db_client=self._db,
            auth_enabled=auth_enabled,
            platform_executor=self.executor,
            platform_registry=self.platform_registry,
            llm_router=self.llm_router,
            _boot_complete=self._running,
            admin_tools=getattr(self, 'admin_tools', None),
            admin_invoker=self.legacy_invoker,
            admin_registry=self.legacy_registry,
            ontology=getattr(self, 'ontology', None),
            tenant_id=self.tenant_id,
            kernel=getattr(self, '_kernel', None),
            credential_store=getattr(self, 'credentials', None),
            environment_manager=getattr(self, '_environment_manager', None),
            env_def_store=getattr(self, '_env_def_store', None),
            env_service=getattr(self, '_env_service', None),
            mcp_manager=getattr(self, '_mcp_manager', None),
            tool_executor=getattr(self, '_tool_executor', None),
        )

    def start_api_server(self, host: str = "0.0.0.0", port: int = 5000, auth_enabled: bool = True):
        """Start the FastAPI server via uvicorn in a background thread."""
        app = self.create_api_app(auth_enabled=auth_enabled)
        if not app:
            logger.warning("API server not available")
            return
        logger.info("API server starting on http://%s:%d (FastAPI + Uvicorn)", host, port)
        import threading
        import uvicorn
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

    async def run_api_server(self, host: str = "0.0.0.0", port: int = 5000, auth_enabled: bool = True):
        """Run uvicorn in the main asyncio loop (for Cloud Run / containers)."""
        app = self.create_api_app(auth_enabled=auth_enabled)
        if not app:
            logger.warning("API server not available")
            return
        logger.info("API server starting on http://%s:%d (FastAPI + Uvicorn, async)", host, port)
        import uvicorn
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()

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


def _validate_config(mode: str) -> list[str]:
    """Validate required configuration before boot. Returns list of errors."""
    errors = []
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_db = bool(os.environ.get("DATABASE_URL"))

    if mode == "autonomous":
        if not has_anthropic and not has_openai:
            errors.append("Autonomous mode requires ANTHROPIC_API_KEY or OPENAI_API_KEY (agents would run simulated)")
        if not has_db:
            errors.append("Autonomous mode requires DATABASE_URL (agents need persistence to survive restarts)")
    elif mode == "supervised":
        if not has_anthropic and not has_openai:
            logger.warning("No LLM API key configured — agents will run in SIMULATED mode")
    return errors


async def main():
    _load_dotenv_from_repo_root()
    parser = argparse.ArgumentParser(description="Boot Helios OS Multi-Stack Platform")
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
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="API listen port (default: env PORT or 5000). Use when 5000 is already in use.",
    )
    parser.add_argument("--no-auth", action="store_true", help="Disable API authentication (dev only)")
    parser.add_argument("--validate-only", action="store_true", help="Validate config and exit")
    args = parser.parse_args()

    # Config validation — fail fast on missing requirements
    config_errors = _validate_config(args.mode)
    if args.validate_only:
        if config_errors:
            for e in config_errors:
                logger.error("CONFIG ERROR: %s", e)
            sys.exit(1)
        else:
            logger.info("Configuration valid for mode=%s", args.mode)
            sys.exit(0)
    if config_errors and args.mode == "autonomous":
        for e in config_errors:
            logger.error("CONFIG ERROR: %s", e)
        logger.error("Fix the above errors or use --mode supervised for dev.")
        sys.exit(1)

    api_port = args.port if args.port is not None else int(os.environ.get("PORT", "5000"))

    bootstrap = PlatformBootstrap(config_path=args.config, mode=args.mode, company_id=args.company)
    await bootstrap.boot(api_listen_port=api_port)

    if args.demo:
        run_demo(company_id=args.company)

    # Store app reference for liveness tick tracking
    _api_app = None
    auth_on = not args.no_auth and not os.environ.get("FORGEOS_AUTH_DISABLED") if args.dashboard else True
    if args.dashboard and args.loop:
        _api_app = bootstrap.create_api_app(auth_enabled=auth_on)
        bootstrap.start_api_server(port=api_port, auth_enabled=auth_on)

    # Register SIGTERM/SIGINT for graceful shutdown
    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_handle_signal(s, bootstrap)))

    if args.loop:
        try:
            await bootstrap.run_main_loop(app=_api_app)
        except asyncio.CancelledError:
            pass
        finally:
            await bootstrap.shutdown()
    elif args.dashboard:
        logger.info("API server running on http://0.0.0.0:%d — Ctrl+C to stop.", api_port)
        try:
            await bootstrap.run_api_server(port=api_port, auth_enabled=auth_on)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await bootstrap.shutdown()


async def _handle_signal(sig, bootstrap):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    logger.info("Received signal %s — initiating graceful shutdown...", sig.name)
    await bootstrap.shutdown()
    # Cancel remaining tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    logger.info("All tasks cancelled. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())
