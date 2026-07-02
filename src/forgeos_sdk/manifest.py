# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Helios OS Agent Manifest Schema.

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

STACKS = Literal["forgeos", "crewai", "adk", "openclaw", "anthropic-agent-sdk", "anthropic-managed", "openai-agents"]
EXECUTION_TYPES = Literal["always_on", "scheduled", "event_driven", "reflex", "autonomous"]
OWNERSHIP_TYPES = Literal["personal", "shared", "client"]
PROVIDERS = Literal["anthropic", "openai", "google", "openclaw", "atlas", "vertex", "vllm"]
MEMORY_BLOCK_TYPES = Literal["persistent", "rolling_window", "shared", "scratch"]


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    """LLM routing config. Model name prefix determines the provider."""

    chat_model: str = Field(..., description="e.g. 'gpt-4o', 'claude-sonnet-4-5-20250514'")
    reasoning_model: str | None = Field(None, description="Optional separate model for reasoning")
    provider: PROVIDERS = Field("anthropic", description="Override auto-detection")
    endpoint: str | None = Field(
        None,
        description=(
            "OpenAI-compatible base URL for a gateway/proxy (e.g. a LiteLLM "
            "router). Used by the atlas/vllm/openai providers; ignored otherwise."
        ),
    )
    api_key_ref: str | None = Field(
        None,
        description=(
            "Reference to the API key — never the raw key. "
            "'secret:<name>' resolves via the encrypted store / GCP Secret "
            "Manager / env; 'env:<VAR>' reads an environment variable."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="e.g. fallback_provider")


class MemoryBlock(BaseModel):
    """A single memory block on an agent (legacy — prefer MemoryMount)."""

    name: str = Field(..., description="Block identifier")
    type: MEMORY_BLOCK_TYPES = Field("persistent")
    max_chars: int = Field(2000)
    max_items: int | None = Field(None, description="For rolling_window type")
    source: str | None = Field(None, description="For shared type, e.g. 'knowledge_base'")
    update_policy: Literal["on_demand", "on_completion", "never"] = "on_demand"


MEMORY_SCOPE = Literal["read-only", "read-write"]


class MemoryMount(BaseModel):
    """A mount point in the agent's file-system memory hierarchy.

    Follows Anthropic's Frontier Memory Architecture: agents work with a
    hierarchical file system they can navigate with familiar tools. Mounts
    define permission scopes (read-only for org knowledge, read-write for
    working memory).
    """

    name: str = Field(..., description="Mount name (becomes a directory in the memory hierarchy)")
    scope: MEMORY_SCOPE = Field("read-write", description="read-only for curated knowledge, read-write for working memory")
    source: str | None = Field(None, description="For read-only mounts: source path or org knowledge ID")
    max_size_kb: int | None = Field(None, description="Optional size limit for this mount")
    description: str = ""


class MemoryConfig(BaseModel):
    """Agent memory configuration.

    Supports both legacy blocks (v1) and file-system mounts (v2).
    When mounts are specified, the agent gets memory__* tools for
    file-system-based knowledge management.
    """

    blocks: list[MemoryBlock] = Field(default_factory=list)
    mounts: list[MemoryMount] = Field(default_factory=list)
    dreaming: bool = Field(False, description="Enable async dreaming — memory curation by a background agent")


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


class ExecCapability(BaseModel):
    """Permission to run shell commands in the agent's execution environment.

    The kernel's ``env.exec`` admission allows a command only when ``enabled`` is
    true (and, if ``allowed_commands`` is set, the command's leading binary is in
    it). A capability token (target ``env:<id>``, verb ``exec``) can override.
    """

    enabled: bool = False
    allowed_commands: list[str] | None = Field(
        None, description="If set, only these leading binaries may run (e.g. [ls, cat, python])"
    )


class Capabilities(BaseModel):
    """What the agent is permitted to do."""

    tools: ToolACL | None = None
    a2a: A2AConfig | None = None
    exec: ExecCapability | None = None


class Environment(BaseModel):
    """A per-agent execution environment: a pod spawned from a Docker image that
    the agent's `env__exec`/`bash` commands run inside (kernel-gated)."""

    image: str = Field(..., description="Docker image, e.g. 'python:3.12'")
    namespace: str | None = Field(None, description="Override the env pod namespace")
    keepalive_cmd: str | None = Field(None, description="Override the pod keepalive command")


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
    """Legacy human-in-the-loop requirement (event-keyed, global).

    DEPRECATED — superseded by :class:`ApprovalRule` (per-tool + heuristic).
    Kept for one release; :class:`Governance` folds any ``human_in_loop``
    entries into ``approvals`` automatically.
    """

    event: str = Field(..., description="Action/event that triggers approval (e.g. 'email.send')")
    approvers: list[str] = Field(default_factory=list, description="Role or user names")
    sla_hours: float = 24.0


APPROVAL_MODES = Literal["always", "never", "conditional"]
APPROVAL_PRIORITIES = Literal["low", "medium", "high", "critical"]
APPROVAL_ON_TIMEOUT = Literal["proceed", "abort", "reask"]


class ApprovalRule(BaseModel):
    """A per-tool / heuristic human-approval rule.

    The kernel matches an agent's ``approvals`` against every tool call. A
    matching rule whose condition fires makes the kernel return ``ask_human``
    instead of executing — the runtime then suspends the agent durably and
    resumes once a human approves (executing the gated tool with a capability
    token). The agent never calls ``ask_human()`` itself; it calls the normal
    tool and the kernel intercepts.

    Examples::

        # Always require approval before sending email.
        - tool: notify__email
          mode: always
          approvers: [ceo-office]
          sla_hours: 4

        # Only when the recipient is external.
        - tool: notify__email
          mode: conditional
          when:
            ask_human_if: {op: not_endswith_any, field: tool_input.to, value: ["@acme.com"]}

        # Only when spend exceeds a threshold.
        - tool: payments__charge
          mode: conditional
          when:
            ask_human_if: {op: gt, field: tool_input.amount_usd, value: 500}
    """

    tool: str = Field(..., description="Tool name or wildcard prefix (e.g. 'notify__*')")
    mode: APPROVAL_MODES = Field("always", description="always | never | conditional")
    when: dict[str, Any] | None = Field(
        None,
        description="JSON-logic clause for conditional mode, e.g. {ask_human_if: {op, field, value}}",
    )
    approvers: list[str] = Field(default_factory=list, description="Roles or user names who may approve")
    sla_hours: float = Field(24.0, description="Approval deadline before escalation/timeout")
    priority: APPROVAL_PRIORITIES = "medium"
    on_timeout: APPROVAL_ON_TIMEOUT = Field(
        "abort", description="What to do if the SLA expires with no human response"
    )
    reason: str = Field("", description="Human-readable rationale shown to the approver")

    @model_validator(mode="after")
    def _require_when_for_conditional(self):
        if self.mode == "conditional" and not self.when:
            raise ValueError("conditional approval rules require a 'when' clause")
        return self


class PolicyRef(BaseModel):
    """A policy: either a file reference (OPA/Rego or JSON-logic) or an inline
    JSON-logic rule declared directly in the manifest.

    Inline rules support tri-state JSON-logic: a ``deny_if`` clause denies the
    action; an ``ask_human_if`` clause routes it through human approval. Both
    may appear in one rule (deny wins)::

        - name: external-email-guard
          inline:
            ask_human_if: {op: not_endswith_any, field: tool_input.to, value: ["@acme.com"]}
    """

    name: str
    ref: str | None = Field(None, description="Path to a .rego or .json policy file")
    inline: dict[str, Any] | None = Field(
        None, description="Inline JSON-logic rule (deny_if / ask_human_if)"
    )

    @model_validator(mode="after")
    def _require_ref_or_inline(self):
        if not self.ref and not self.inline:
            raise ValueError("policy must declare either 'ref' or 'inline'")
        return self


class Governance(BaseModel):
    """Governance rules (audit, approval, signatures, policies)."""

    approvals: list[ApprovalRule] = Field(
        default_factory=list,
        description="Per-tool / heuristic human-approval rules (kernel-enforced)",
    )
    human_in_loop: list[HITLApproval] = Field(
        default_factory=list, description="DEPRECATED — folded into 'approvals'"
    )
    policies: list[PolicyRef] = Field(default_factory=list)
    audit_level: Literal["none", "basic", "full"] = "full"
    signing_required: bool = False

    @model_validator(mode="after")
    def _fold_legacy_hitl(self):
        """Map any legacy ``human_in_loop`` entries into ``approvals`` so the
        kernel only has to read one shape. The legacy ``event`` becomes the
        ``tool`` match (events were already action-keyed)."""
        if self.human_in_loop and not self.approvals:
            self.approvals = [
                ApprovalRule(
                    tool=h.event,
                    mode="always",
                    approvers=h.approvers,
                    sla_hours=h.sla_hours,
                )
                for h in self.human_in_loop
            ]
        return self


class DriveConfig(BaseModel):
    """Per-agent Google Drive identity + context folder.

    The agent impersonates its own service account (``service_account``) to
    access Drive — keyless, ``drive.file`` scope, so the SA only sees what the
    user explicitly shares with its email. ``folder_id`` is the default context
    folder (shared with that SA); drive tools default reads/writes to it.
    Surfaced in the dashboard so the user knows which SA email to share with.
    """

    service_account: str | None = Field(
        None, description="This agent's SA email (share the Drive folder with it)"
    )
    folder_id: str | None = Field(
        None, description="Default Drive context folder id (shared with the SA)"
    )
    access: Literal["read", "readwrite"] = Field(
        "read", description="read = context only; readwrite = also write reports back"
    )
    provision: bool = Field(
        False,
        description="If true, the platform auto-creates this SA (using service_account's "
        "local-part as the id) in the current project on deploy, grants the runtime SA "
        "token-creator on it, and deletes it on undeploy.",
    )
    provisioned: bool = Field(
        False,
        description="Set by the platform after it auto-created the SA (internal marker; "
        "gates deletion so user-supplied SAs are never removed).",
    )


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


class Scope(BaseModel):
    """Organizational taxonomy — where this agent sits in the company.

    Used by the kernel to resolve hierarchical policies (Global > Namespace >
    Agent) and by RAG pipelines to filter knowledge by department/team/role.
    """

    department: str = Field("", description="Department (finance, hr, sales, engineering, ...)")
    team: str = Field("", description="Team within department (treasury, payroll, ...)")
    role: str = Field("", description="Job role this agent serves (treasury-analyst, ...)")
    job_id: str = Field("", description="Internal job code (e.g. TRS-001)")


class KnowledgeSource(BaseModel):
    """A knowledge source the agent may access."""

    path: str = Field(..., description="Path or URI (e.g. knowledge/departments/finance/)")
    type: Literal["markdown", "rag", "google_sheet", "google_doc", "database", "api"] = "markdown"
    description: str = ""


class Knowledge(BaseModel):
    """Knowledge scoping — what data this agent can see.

    Controls RAG retrieval filters and declares which knowledge sources
    (Wiki.js paths, Google Sheets, databases) the agent may access.
    """

    rag_filter: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata filter applied to RAG queries (e.g. {department: finance, team: treasury})",
    )
    allowed_sources: list[str] = Field(
        default_factory=list,
        description="Paths the agent may read (e.g. knowledge/departments/finance/)",
    )
    blocked_sources: list[str] = Field(
        default_factory=list,
        description="Paths explicitly denied",
    )
    sources: list[KnowledgeSource] = Field(
        default_factory=list,
        description="Typed knowledge source declarations",
    )


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
    drive: DriveConfig | None = Field(None, description="Per-agent Drive SA + context folder")
    dependencies: Dependencies | None = None
    scope: Scope | None = Field(None, description="Organizational taxonomy (department, team, role)")
    knowledge: Knowledge | None = Field(None, description="Knowledge scoping (RAG filters, allowed sources)")
    environment: Environment | None = Field(None, description="Per-agent execution environment (pod from a Docker image)")

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
    The complete agent manifest. Every agent in Helios OS is an instance of this.

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

    # ---- Canonical structured representation ----------------------------
    #
    # The plan calls for deleting the ``_memory`` / ``_guardrails`` / etc.
    # metadata-bag keys entirely. Doing so in one shot would break every
    # consumer that reads those keys (dashboard, kernel admission, A2A
    # handler, persistence — 16 files today). The consolidation is done
    # in two steps:
    #
    #   1. Introduce ``canonical_dict()`` — a structured output where v2
    #      sections are first-class top-level keys (no ``_memory`` bag,
    #      etc.). New consumers read from this shape.
    #   2. Follow-up: migrate the 16 readers off the bag, then delete
    #      ``to_deploy_request()``'s bag-packing.
    #
    # ``to_deploy_request()`` below keeps the bag for backward compatibility
    # until step 2 lands.

    def canonical_dict(self, base_path: Path | None = None) -> dict:
        """Return a structured, lossless representation of the manifest.

        Shape (top-level keys):

            apiVersion, kind, metadata, spec

        Where ``spec`` contains v2 sections as first-class nested dicts
        (``memory``, ``guardrails``, ``runtime``, ``lifecycle``,
        ``capabilities``, ``boundaries``, ``triggers``, ``governance``,
        ``dependencies``, ``observability``) rather than being smuggled
        through ``_memory``/``_guardrails``/... bag keys.

        This is the new reader-facing representation. ``to_deploy_request``
        remains wire-compatible with existing consumers during migration.
        """
        prompt_text = ""
        if isinstance(self.spec.system_prompt, str):
            prompt_text = self.spec.system_prompt
        elif isinstance(self.spec.system_prompt, SystemPrompt):
            prompt_text = self.spec.system_prompt.resolve(base_path)

        effective_stack = self.spec.runtime.framework if self.spec.runtime else self.spec.stack
        effective_exec_type = (
            self.spec.lifecycle.type if self.spec.lifecycle else self.spec.execution_type
        )
        effective_schedule = (
            self.spec.lifecycle.schedule
            if self.spec.lifecycle and self.spec.lifecycle.schedule
            else self.spec.schedule
        )
        effective_event_triggers = (
            self.spec.lifecycle.event_triggers
            if self.spec.lifecycle and self.spec.lifecycle.event_triggers
            else self.spec.event_triggers
        )
        effective_goal = (
            self.spec.lifecycle.goal
            if self.spec.lifecycle and self.spec.lifecycle.goal
            else self.spec.goal
        )
        if self.spec.capabilities and self.spec.capabilities.tools:
            effective_tools = list(self.spec.capabilities.tools.allowed)
        else:
            effective_tools = list(self.spec.tools)

        spec_out: dict[str, Any] = {
            "stack": effective_stack,
            "execution_type": effective_exec_type,
            "ownership": self.spec.ownership,
            "owner_id": self.spec.owner_id or None,
            "llm": self.spec.llm.model_dump(),
            "tools": effective_tools,
            "schedule": effective_schedule,
            "event_triggers": list(effective_event_triggers),
            "goal": effective_goal,
            "system_prompt": prompt_text,
            # Optional user-defined metadata dict (NOT the legacy bag).
            "metadata": dict(self.spec.metadata),
        }

        # v2 sections as first-class keys — no ``_memory`` smuggling.
        if self.spec.memory:
            spec_out["memory"] = self.spec.memory.model_dump()
        if self.spec.guardrails:
            spec_out["guardrails"] = self.spec.guardrails.model_dump()
        if self.spec.observability:
            spec_out["observability"] = self.spec.observability.model_dump()
        if self.spec.runtime:
            spec_out["runtime"] = self.spec.runtime.model_dump()
        if self.spec.lifecycle:
            spec_out["lifecycle"] = self.spec.lifecycle.model_dump()
        if self.spec.capabilities:
            spec_out["capabilities"] = self.spec.capabilities.model_dump()
        if self.spec.boundaries:
            spec_out["boundaries"] = self.spec.boundaries.model_dump()
        if self.spec.triggers:
            spec_out["triggers"] = [t.model_dump() for t in self.spec.triggers]
        if self.spec.governance:
            spec_out["governance"] = self.spec.governance.model_dump()
        if self.spec.dependencies:
            spec_out["dependencies"] = self.spec.dependencies.model_dump()

        return {
            "apiVersion": self.apiVersion,
            "kind": self.kind,
            "metadata": self.metadata.model_dump(),
            "spec": spec_out,
        }

    def to_deploy_request(self, base_path: Path | None = None) -> dict:
        """
        Convert to the POST /api/platform/agents request body.

        Bridges rich manifests (v2 AgentOS) to the flat API the platform accepts.
        V2 fields (lifecycle, capabilities, boundaries, etc.) are flattened into
        the corresponding v1 fields and archived in metadata for round-trip.

        DEPRECATED DIRECTION: the ``_memory`` / ``_guardrails`` / etc. bag
        keys below are read by 16 files today. New readers should consume
        :meth:`canonical_dict` instead. This method stays wire-compatible
        until those readers migrate.
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
        if self.spec.drive:
            extra_metadata["_drive"] = self.spec.drive.model_dump()
        if self.spec.dependencies:
            extra_metadata["_dependencies"] = self.spec.dependencies.model_dump()
        if self.spec.environment:
            extra_metadata["_environment"] = self.spec.environment.model_dump()
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
            "endpoint": self.spec.llm.endpoint,
            "api_key_ref": self.spec.llm.api_key_ref,
            "llm_metadata": dict(self.spec.llm.metadata or {}),
            "tools": effective_tools,
            "schedule": effective_schedule,
            "event_triggers": effective_event_triggers,
            "goal": effective_goal,
            "system_prompt": prompt_text,
            "metadata": extra_metadata,
        }


# ---------------------------------------------------------------------------
# Team Manifest — deploy multi-agent teams from a single YAML
# ---------------------------------------------------------------------------

TEAM_ROLES = Literal["supervisor", "worker", "specialist", "curator"]
TEAM_ORCHESTRATION = Literal["supervisor", "parallel", "sequential", "mesh"]


class TeamAgentSpec(BaseModel):
    """One agent within a team. Inherits defaults from the team spec."""

    name: str = Field(..., min_length=2, max_length=64)
    role: TEAM_ROLES = "worker"
    description: str = ""

    stack: STACKS | None = None
    execution_type: EXECUTION_TYPES | None = None
    llm: LLMConfig | None = None
    tools: list[str] = Field(default_factory=list)
    system_prompt: str | None = None
    schedule: str | None = None
    event_triggers: list[str] = Field(default_factory=list)
    goal: str = ""
    boundaries: Boundaries | None = None
    guardrails: Guardrails | None = None
    memory: MemoryConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamDefaults(BaseModel):
    """Default values applied to every agent in the team unless overridden."""

    stack: STACKS = "forgeos"
    llm: LLMConfig | None = None
    boundaries: Boundaries | None = None
    guardrails: Guardrails | None = None


class SharedContext(BaseModel):
    """Shared context available to all agents in the team."""

    namespace: str = "default"
    shared_tools: list[str] = Field(default_factory=list)


class TeamSpec(BaseModel):
    """Team specification — orchestration pattern + agent list."""

    defaults: TeamDefaults = Field(default_factory=TeamDefaults)
    orchestration: TEAM_ORCHESTRATION = "supervisor"
    agents: list[TeamAgentSpec] = Field(..., min_length=1)
    shared_context: SharedContext | None = None

    @model_validator(mode="after")
    def _validate_supervisor(self):
        if self.orchestration == "supervisor":
            supervisors = [a for a in self.agents if a.role == "supervisor"]
            if len(supervisors) == 0:
                raise ValueError(
                    "supervisor orchestration requires at least one agent "
                    "with role: supervisor"
                )
            if len(supervisors) > 1:
                raise ValueError(
                    "supervisor orchestration allows only one supervisor"
                )
        return self


class TeamManifest(BaseModel):
    """Deploy a team of agents from a single YAML file.

    Supports four orchestration patterns:
    - supervisor: boss delegates to workers via A2A
    - parallel: all agents deploy independently, shared namespace
    - sequential: pipeline — each agent feeds the next
    - mesh: full-mesh A2A ACLs, everyone can call everyone
    """

    apiVersion: Literal["forgeos/v1", "agentos/v1"] = "forgeos/v1"
    kind: Literal["Team"] = "Team"
    metadata: Metadata
    spec: TeamSpec

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TeamManifest":
        import yaml
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> "TeamManifest":
        return cls.model_validate(data)


def load_manifest(path: str | Path) -> AgentManifest | TeamManifest:
    """Load a manifest YAML and return the correct type based on 'kind'."""
    import yaml
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    kind = data.get("kind", "Agent")
    if kind == "Team":
        return TeamManifest.model_validate(data)
    return AgentManifest.model_validate(data)


# ---------------------------------------------------------------------------
# V2-section reader — transparent access to v2 fields during migration
# ---------------------------------------------------------------------------

# Mapping from v2 section name to the legacy metadata-bag key.
_V2_BAG_KEYS: dict[str, str] = {
    "memory": "_memory",
    "guardrails": "_guardrails",
    "observability": "_observability",
    "runtime": "_runtime",
    "lifecycle": "_lifecycle",
    "capabilities": "_capabilities",
    "boundaries": "_boundaries",
    "triggers": "_triggers",
    "governance": "_governance",
    "drive": "_drive",
    "dependencies": "_dependencies",
    "namespace": "_namespace",
    "agent_version": "_agent_version",
    "labels": "_labels",
    "annotations": "_annotations",
}


def read_v2_section(
    source: dict | Any,
    section: str,
    default: Any = None,
) -> Any:
    """Read a v2 section from either the canonical shape or the legacy bag.

    Accepts:
      * a dict (raw deploy-request dict or manifest.spec dict)
      * any object with a ``metadata`` attribute (e.g. ``AgentDefinition``)

    The reader checks, in order:
      1. ``source[section]`` or ``source.section`` (canonical first-class)
      2. ``source["metadata"][_bag_key]`` or ``source.metadata[_bag_key]``

    Returns ``default`` if neither is present.

    This is the single chokepoint for v2 reads during the bag→canonical
    migration. Consumers that call this helper become automatically
    compatible with both shapes, and the helper gives us a single place
    to emit a deprecation warning once the bag is ready to be deleted.
    """
    if section not in _V2_BAG_KEYS:
        raise KeyError(f"unknown v2 section: {section!r}")

    bag_key = _V2_BAG_KEYS[section]

    # Source is a plain dict — try first-class at this level.
    if isinstance(source, dict):
        if section in source and source[section] is not None:
            return source[section]
        metadata = source.get("metadata") or {}
        if isinstance(metadata, dict) and bag_key in metadata:
            return metadata[bag_key]
        # Nested under spec (canonical manifest dict)
        spec = source.get("spec")
        if isinstance(spec, dict):
            if section in spec and spec[section] is not None:
                return spec[section]
        return default

    # Source is an object (e.g. AgentDefinition).
    if hasattr(source, section):
        value = getattr(source, section)
        if value is not None:
            return value
    metadata = getattr(source, "metadata", None) or {}
    if isinstance(metadata, dict) and bag_key in metadata:
        return metadata[bag_key]
    return default
