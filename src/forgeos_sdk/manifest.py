"""
ForgeOS Agent Manifest Schema.

Defines the canonical agent.yaml format. Every agent — whether declared in
Python code, a YAML file, or an API call — ultimately reduces to this schema.

Wire format (agent.yaml):

    apiVersion: forgeos/v1
    kind: Agent
    metadata:
      name: email-checker
      department: operations
      description: "Checks email on a schedule"
    spec:
      stack: forgeos
      execution_type: scheduled
      schedule: "0 7,12,17 * * *"
      llm:
        chat_model: gpt-4o
        provider: openai
      tools:
        - mcp__filesystem__*
        - company__publish_event
      system_prompt: ./prompts/email.md
      memory:
        blocks:
          - name: user_prefs
            type: persistent
      guardrails:
        max_tokens_per_run: 10000
        max_cost_usd_per_day: 5.00
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerated primitives
# ---------------------------------------------------------------------------

STACKS = Literal["forgeos", "crewai", "adk", "openclaw"]
EXECUTION_TYPES = Literal["always_on", "scheduled", "event_driven", "reflex", "autonomous"]
OWNERSHIP_TYPES = Literal["personal", "shared", "client"]
PROVIDERS = Literal["anthropic", "openai", "google", "openclaw"]
MEMORY_BLOCK_TYPES = Literal["persistent", "rolling_window", "shared", "scratch"]


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    """LLM routing config. Model name prefix determines the provider."""

    chat_model: str = Field(..., description="e.g. 'gpt-4o', 'claude-sonnet-4-5-20250514'")
    reasoning_model: str | None = Field(None, description="Optional separate model for reasoning")
    provider: PROVIDERS = Field("anthropic", description="Override auto-detection")
    metadata: dict[str, Any] = Field(default_factory=dict, description="e.g. fallback_provider")


class MemoryBlock(BaseModel):
    """A single memory block on an agent."""

    name: str = Field(..., description="Block identifier")
    type: MEMORY_BLOCK_TYPES = Field("persistent")
    max_chars: int = Field(2000)
    max_items: int | None = Field(None, description="For rolling_window type")
    source: str | None = Field(None, description="For shared type, e.g. 'knowledge_base'")
    update_policy: Literal["on_demand", "on_completion", "never"] = "on_demand"


class MemoryConfig(BaseModel):
    """Structured memory for the agent (separate from session history)."""

    blocks: list[MemoryBlock] = Field(default_factory=list)


class Guardrails(BaseModel):
    """Policies that constrain the agent's behavior."""

    max_tokens_per_run: int | None = None
    max_cost_usd_per_day: float | None = None
    max_tool_calls_per_run: int | None = None
    content_filter: Literal["none", "default", "strict"] = "default"
    allowed_models: list[str] | None = Field(None, description="If set, restrict to these models")


class Observability(BaseModel):
    """Tracing and observability config."""

    trace: Literal["none", "langfuse", "langsmith", "datadog"] = "none"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    emit_metrics: bool = True


# ---------------------------------------------------------------------------
# AgentOS kernel primitives (Tier 0 additions)
# ---------------------------------------------------------------------------

class A2APeer(BaseModel):
    """A peer (agent or group) allowed to call this agent, or callable by it."""

    namespace: str = "default"
    agents: list[str] = Field(default_factory=list, description="Specific agent names")
    roles: list[str] = Field(default_factory=list, description="Role-based access (e.g. 'manager')")
    labels: dict[str, str] = Field(default_factory=dict, description="Label selector")


class A2AConfig(BaseModel):
    """Agent-to-agent communication ACLs."""

    canCall: list[A2APeer] = Field(default_factory=list, description="Who this agent may call")
    canBeCalledBy: list[A2APeer] = Field(default_factory=list, description="Who may call this agent")
    max_depth: int = Field(5, description="Max delegation chain depth")


class ToolACL(BaseModel):
    """Fine-grained tool access control."""

    allowed: list[str] = Field(default_factory=list, description="Exact names or wildcard prefixes")
    denied: list[str] = Field(default_factory=list, description="Deny list (evaluated after allowed)")


class Capabilities(BaseModel):
    """What the agent is permitted to do."""

    tools: ToolACL | None = None
    a2a: A2AConfig | None = None


class DataBoundaries(BaseModel):
    """Data access boundaries."""

    allowed_namespaces: list[str] = Field(default_factory=list)
    blocked_namespaces: list[str] = Field(default_factory=list)
    pii_policy: Literal["allow", "detect", "mask", "redact", "block"] = "detect"


class Budgets(BaseModel):
    """Economic resource limits."""

    daily_usd: float | None = None
    per_task_usd: float | None = None
    max_tokens_per_run: int | None = None
    max_tool_calls_per_run: int | None = None
    max_concurrent_tasks: int = 1


class Boundaries(BaseModel):
    """All resource limits (cgroups equivalent)."""

    budgets: Budgets = Field(default_factory=Budgets)
    data: DataBoundaries = Field(default_factory=DataBoundaries)


class Trigger(BaseModel):
    """A unified trigger (cron, webhook, or event)."""

    name: str = ""
    cron: str | None = None
    webhook: str | None = None
    event: str | None = None
    filter: str | None = Field(None, description="Event filter expression (e.g. 'subject contains sales')")


class HITLApproval(BaseModel):
    """Human-in-the-loop approval requirement."""

    event: str = Field(..., description="Action/event that triggers approval (e.g. 'email.send')")
    approvers: list[str] = Field(default_factory=list, description="Role or user names")
    sla_hours: float = 24.0


class PolicyRef(BaseModel):
    """Reference to a policy file (OPA/Rego or JSON-logic)."""

    name: str
    ref: str = Field(..., description="Path to .rego or .json policy file")


class Governance(BaseModel):
    """Governance rules (audit, approval, signatures, policies)."""

    human_in_loop: list[HITLApproval] = Field(default_factory=list)
    policies: list[PolicyRef] = Field(default_factory=list)
    audit_level: Literal["none", "basic", "full"] = "full"
    signing_required: bool = False


class AgentDependency(BaseModel):
    """A dependency on another agent (like systemd 'After=' or Helm 'dependsOn')."""

    namespace: str = "default"
    name: str
    optional: bool = False
    min_version: str | None = None


class Dependencies(BaseModel):
    """Runtime dependencies on other agents or tools."""

    agents: list[AgentDependency] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list, description="Required MCP server names")


class Runtime(BaseModel):
    """Which framework runs this agent."""

    framework: Literal["forgeos", "crewai", "adk", "openclaw", "langgraph"] = "forgeos"
    image: str | None = Field(None, description="Versioned artifact reference (e.g. 'sales-agent:2.1.0')")
    image_pull_policy: Literal["Always", "IfNotPresent", "Never"] = "IfNotPresent"


class Lifecycle(BaseModel):
    """Agent lifecycle specification (supersedes the flat execution_type)."""

    type: EXECUTION_TYPES = "reflex"
    replicas: int = Field(1, description="Desired number of running instances")
    restart_policy: Literal["Always", "OnFailure", "Never"] = "OnFailure"
    schedule: str | None = Field(None, description="Cron expression for scheduled type")
    event_triggers: list[str] = Field(default_factory=list)
    goal: str = Field("", description="For autonomous type")


class AgentCondition(BaseModel):
    """A readiness/health condition (k8s-style)."""

    type: str = Field(..., description="Ready, ToolsResolved, DependenciesMet, etc.")
    status: Literal["True", "False", "Unknown"] = "Unknown"
    last_transition: str | None = None
    reason: str | None = None
    message: str | None = None


class AgentStatus(BaseModel):
    """Runtime status filled by the controller (read-only from manifest POV)."""

    phase: Literal["Pending", "Running", "Succeeded", "Failed", "Quarantined", "Unknown"] = "Pending"
    conditions: list[AgentCondition] = Field(default_factory=list)
    current_activity: str | None = None
    last_run_at: str | None = None
    runs_today: int = 0
    cost_today_usd: float = 0.0
    avg_latency_ms: float = 0.0
    observed_generation: int = 0


class SystemPrompt(BaseModel):
    """Rich system prompt — can be a string, a file reference, or templated."""

    content: str | None = None
    file: str | None = Field(None, description="Relative path to a .md or .txt prompt file")
    variables: dict[str, str] = Field(default_factory=dict, description="Template variables")
    template_engine: Literal["none", "jinja2"] = "none"

    @model_validator(mode="after")
    def _one_source(self):
        if self.content and self.file:
            raise ValueError("Specify either 'content' OR 'file', not both")
        if not self.content and not self.file:
            raise ValueError("Must specify 'content' or 'file'")
        return self

    def resolve(self, base_path: Path | None = None) -> str:
        """Return the fully-resolved prompt text."""
        if self.content:
            text = self.content
        else:
            prompt_path = Path(self.file)
            if not prompt_path.is_absolute() and base_path:
                prompt_path = base_path / prompt_path
            text = prompt_path.read_text(encoding="utf-8")

        if self.variables and self.template_engine == "jinja2":
            try:
                from jinja2 import Template
                text = Template(text).render(**self.variables)
            except ImportError:
                pass  # jinja2 not installed, return raw
        return text


# ---------------------------------------------------------------------------
# Top-level schemas
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    """Agent identification — the 'who'.

    Follows Kubernetes metadata conventions: name + namespace + uid + generation
    + labels + annotations. Namespaces provide logical isolation between teams.
    """

    name: str = Field(..., min_length=2, max_length=64)
    namespace: str = Field("default", description="Logical isolation group (like k8s namespaces)")
    uid: str | None = Field(None, description="Stable agent ID (auto-generated if omitted)")
    version: str = Field("1.0.0", description="Semver version of this agent's spec")
    generation: int = Field(1, description="Increments on every spec change (set by controller)")
    description: str = ""
    department: str = ""
    labels: dict[str, str] = Field(default_factory=dict, description="k8s-style labels for selection")
    annotations: dict[str, str] = Field(default_factory=dict, description="Non-identifying metadata (signatures, audit refs)")

    @field_validator("name", "namespace")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", v):
            raise ValueError(
                "Name/namespace must start with a letter and contain only alphanumeric, "
                "hyphens, or underscores"
            )
        return v


class Spec(BaseModel):
    """Agent runtime specification — the 'what and how'.

    Backward-compatible: v1 fields (stack, execution_type, tools as list, schedule,
    event_triggers, goal) remain. New v2/AgentOS fields (runtime, lifecycle,
    capabilities, boundaries, triggers, governance, dependencies) are all optional.
    """

    # --- v1 flat fields (backward compat) ---
    stack: STACKS = "forgeos"
    execution_type: EXECUTION_TYPES = "reflex"
    ownership: OWNERSHIP_TYPES = "shared"
    owner_id: str | None = None

    llm: LLMConfig

    # Lifecycle triggers (v1)
    schedule: str | None = Field(None, description="Cron expression for scheduled agents")
    event_triggers: list[str] = Field(default_factory=list)
    goal: str = Field("", description="For autonomous agents")

    # Capabilities (v1)
    tools: list[str] = Field(default_factory=list, description="Tool whitelist with wildcard support")
    system_prompt: SystemPrompt | str | None = None

    # --- v2 AgentOS kernel primitives (all optional) ---
    runtime: Runtime | None = Field(None, description="Framework + image (overrides 'stack')")
    lifecycle: Lifecycle | None = Field(None, description="Rich lifecycle (overrides execution_type)")
    capabilities: Capabilities | None = Field(None, description="Tool ACLs + A2A peers (overrides 'tools')")
    boundaries: Boundaries | None = Field(None, description="Budgets + data boundaries")
    triggers: list[Trigger] = Field(default_factory=list, description="Unified trigger list")
    governance: Governance | None = None
    dependencies: Dependencies | None = None

    # Advanced (shared v1/v2)
    memory: MemoryConfig | None = None
    guardrails: Guardrails | None = None
    observability: Observability | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary agent config")

    @model_validator(mode="after")
    def _validate_execution_type_requirements(self):
        """Enforce per-execution-type requirements."""
        if self.execution_type == "scheduled" and not self.schedule:
            raise ValueError("scheduled agents require a 'schedule' cron expression")
        if self.execution_type == "event_driven" and not self.event_triggers:
            raise ValueError("event_driven agents require at least one 'event_triggers' entry")
        if self.execution_type == "autonomous" and not self.goal:
            raise ValueError("autonomous agents require a 'goal' description")
        if self.ownership == "client" and not self.owner_id:
            raise ValueError("client-owned agents require 'owner_id' (the client ID)")
        return self


class AgentManifest(BaseModel):
    """
    The complete agent manifest. Every agent in ForgeOS is an instance of this.

    This is the wire format for agent.yaml files and the target schema that
    Python SDK builders / decorators compile to.

    Supports two apiVersions:
      - forgeos/v1  — flat v1 spec (original, still supported)
      - agentos/v1  — full AgentOS kernel-style spec (namespaces, A2A, policies,
                      lifecycle, dependencies, status)
    """

    apiVersion: Literal["forgeos/v1", "agentos/v1"] = "forgeos/v1"
    kind: Literal["Agent", "AgentContract"] = "Agent"
    metadata: Metadata
    spec: Spec
    status: AgentStatus | None = Field(None, description="Runtime status (filled by controller, not user)")

    # ---- Loaders ----------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentManifest":
        """Load a manifest from a YAML file."""
        import yaml
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, path: str | Path) -> "AgentManifest":
        """Load a manifest from a JSON file."""
        import json
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentManifest":
        """Validate and build a manifest from a dict."""
        return cls.model_validate(data)

    # ---- Wire-format converters ------------------------------------------

    def to_deploy_request(self, base_path: Path | None = None) -> dict:
        """
        Convert to the POST /api/platform/agents request body.

        Bridges rich manifests (v2 AgentOS) to the flat API the platform accepts.
        V2 fields (lifecycle, capabilities, boundaries, etc.) are flattened into
        the corresponding v1 fields and archived in metadata for round-trip.
        """
        # Resolve system prompt to plain text
        prompt_text = ""
        if isinstance(self.spec.system_prompt, str):
            prompt_text = self.spec.system_prompt
        elif isinstance(self.spec.system_prompt, SystemPrompt):
            prompt_text = self.spec.system_prompt.resolve(base_path)

        # Resolve v2 → v1 fields (prefer v2 if present, fallback to v1)
        effective_stack = self.spec.runtime.framework if self.spec.runtime else self.spec.stack
        effective_exec_type = self.spec.lifecycle.type if self.spec.lifecycle else self.spec.execution_type
        effective_schedule = (
            self.spec.lifecycle.schedule if self.spec.lifecycle and self.spec.lifecycle.schedule
            else self.spec.schedule
        )
        effective_event_triggers = (
            self.spec.lifecycle.event_triggers if self.spec.lifecycle and self.spec.lifecycle.event_triggers
            else self.spec.event_triggers
        )
        effective_goal = (
            self.spec.lifecycle.goal if self.spec.lifecycle and self.spec.lifecycle.goal
            else self.spec.goal
        )
        # Merge tools from v2 capabilities.tools.allowed or fallback to v1 tools
        if self.spec.capabilities and self.spec.capabilities.tools:
            effective_tools = self.spec.capabilities.tools.allowed
        else:
            effective_tools = self.spec.tools

        # Pack rich v2 sections into metadata for round-trip
        extra_metadata = dict(self.spec.metadata)
        if self.spec.memory:
            extra_metadata["_memory"] = self.spec.memory.model_dump()
        if self.spec.guardrails:
            extra_metadata["_guardrails"] = self.spec.guardrails.model_dump()
        if self.spec.observability:
            extra_metadata["_observability"] = self.spec.observability.model_dump()
        # AgentOS v2 extras
        if self.spec.runtime:
            extra_metadata["_runtime"] = self.spec.runtime.model_dump()
        if self.spec.lifecycle:
            extra_metadata["_lifecycle"] = self.spec.lifecycle.model_dump()
        if self.spec.capabilities:
            extra_metadata["_capabilities"] = self.spec.capabilities.model_dump()
        if self.spec.boundaries:
            extra_metadata["_boundaries"] = self.spec.boundaries.model_dump()
        if self.spec.triggers:
            extra_metadata["_triggers"] = [t.model_dump() for t in self.spec.triggers]
        if self.spec.governance:
            extra_metadata["_governance"] = self.spec.governance.model_dump()
        if self.spec.dependencies:
            extra_metadata["_dependencies"] = self.spec.dependencies.model_dump()
        # Metadata identity
        extra_metadata["_namespace"] = self.metadata.namespace
        extra_metadata["_agent_version"] = self.metadata.version
        extra_metadata["_labels"] = self.metadata.labels
        extra_metadata["_annotations"] = self.metadata.annotations

        return {
            "name": self.metadata.name,
            "stack": effective_stack,
            "execution_type": effective_exec_type,
            "ownership": self.spec.ownership,
            "owner_id": self.spec.owner_id or "",
            "description": self.metadata.description,
            "department": self.metadata.department,
            "chat_model": self.spec.llm.chat_model,
            "provider": self.spec.llm.provider,
            "tools": effective_tools,
            "schedule": effective_schedule,
            "event_triggers": effective_event_triggers,
            "goal": effective_goal,
            "system_prompt": prompt_text,
            "metadata": extra_metadata,
        }
