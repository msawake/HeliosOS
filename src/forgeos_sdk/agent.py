"""
ForgeOS Agent builder — ergonomic Python APIs that compile to an AgentManifest.

Two styles:

**Builder (chainable, functional):**

    from forgeos_sdk import Agent

    manifest = (Agent.builder("email-checker")
        .forgeos()
        .scheduled("0 7,12,17 * * *")
        .model("gpt-4o", provider="openai")
        .tools("mcp__filesystem__*", "company__publish_event")
        .prompt("You check email and summarize...")
        .department("operations")
        .build())

**Class (declarative, CrewAI-style):**

    from forgeos_sdk import Agent

    class EmailChecker(Agent):
        name = "email-checker"
        stack = "forgeos"
        execution_type = "scheduled"
        schedule = "0 7,12,17 * * *"
        model = "gpt-4o"
        provider = "openai"
        tools = ["mcp__filesystem__*", "company__publish_event"]
        system_prompt = "You check email and summarize..."
        department = "operations"

    manifest = EmailChecker.manifest()

Both produce the same `AgentManifest` — just pick your style.
"""

from __future__ import annotations

from typing import Any

from .manifest import (
    AgentManifest,
    Guardrails,
    LLMConfig,
    MemoryBlock,
    MemoryConfig,
    Metadata,
    Observability,
    Spec,
    SystemPrompt,
)


class AgentBuilder:
    """Fluent builder that compiles to an AgentManifest."""

    def __init__(self, name: str):
        self._name = name
        self._description = ""
        self._department = ""
        self._labels: dict[str, str] = {}

        self._stack: str = "forgeos"
        self._execution_type: str = "reflex"
        self._ownership: str = "shared"
        self._owner_id: str | None = None

        self._llm: dict = {"chat_model": "claude-sonnet-4-5-20250514", "provider": "anthropic"}

        self._schedule: str | None = None
        self._event_triggers: list[str] = []
        self._goal: str = ""

        self._tools: list[str] = []
        self._system_prompt: SystemPrompt | str | None = None

        self._memory: MemoryConfig | None = None
        self._guardrails: Guardrails | None = None
        self._observability: Observability | None = None
        self._metadata: dict[str, Any] = {}

    # ---- Stack ------------------------------------------------------------
    def forgeos(self) -> "AgentBuilder":
        self._stack = "forgeos"
        return self

    def crewai(self) -> "AgentBuilder":
        self._stack = "crewai"
        return self

    def adk(self) -> "AgentBuilder":
        self._stack = "adk"
        return self

    def openclaw(self) -> "AgentBuilder":
        self._stack = "openclaw"
        return self

    def stack(self, stack: str) -> "AgentBuilder":
        self._stack = stack
        return self

    # ---- Execution type ---------------------------------------------------
    def always_on(self) -> "AgentBuilder":
        self._execution_type = "always_on"
        return self

    def scheduled(self, cron: str) -> "AgentBuilder":
        self._execution_type = "scheduled"
        self._schedule = cron
        return self

    def event_driven(self, *events: str) -> "AgentBuilder":
        self._execution_type = "event_driven"
        self._event_triggers = list(events)
        return self

    def reflex(self) -> "AgentBuilder":
        self._execution_type = "reflex"
        return self

    def autonomous(self, goal: str) -> "AgentBuilder":
        self._execution_type = "autonomous"
        self._goal = goal
        return self

    # ---- Ownership --------------------------------------------------------
    def personal(self, owner_id: str) -> "AgentBuilder":
        self._ownership = "personal"
        self._owner_id = owner_id
        return self

    def shared(self) -> "AgentBuilder":
        self._ownership = "shared"
        return self

    def client(self, client_id: str) -> "AgentBuilder":
        self._ownership = "client"
        self._owner_id = client_id
        return self

    # ---- LLM --------------------------------------------------------------
    def model(self, chat_model: str, provider: str | None = None, fallback: str | None = None) -> "AgentBuilder":
        self._llm["chat_model"] = chat_model
        if provider:
            self._llm["provider"] = provider
        else:
            # Auto-detect from prefix
            if chat_model.startswith("claude-"):
                self._llm["provider"] = "anthropic"
            elif chat_model.startswith(("gpt-", "o1-", "o3-")):
                self._llm["provider"] = "openai"
        if fallback:
            self._llm["metadata"] = {"fallback_provider": fallback}
        return self

    # ---- Tools & prompt ---------------------------------------------------
    def tools(self, *tool_names: str) -> "AgentBuilder":
        self._tools = list(tool_names)
        return self

    def add_tool(self, tool_name: str) -> "AgentBuilder":
        self._tools.append(tool_name)
        return self

    def prompt(self, text: str) -> "AgentBuilder":
        self._system_prompt = text
        return self

    def prompt_from_file(self, file_path: str, variables: dict | None = None) -> "AgentBuilder":
        self._system_prompt = SystemPrompt(
            file=file_path,
            variables=variables or {},
            template_engine="jinja2" if variables else "none",
        )
        return self

    # ---- Metadata ---------------------------------------------------------
    def description(self, desc: str) -> "AgentBuilder":
        self._description = desc
        return self

    def department(self, dept: str) -> "AgentBuilder":
        self._department = dept
        return self

    def label(self, key: str, value: str) -> "AgentBuilder":
        self._labels[key] = value
        return self

    def metadata(self, **kwargs) -> "AgentBuilder":
        self._metadata.update(kwargs)
        return self

    # ---- Advanced ---------------------------------------------------------
    def memory_block(self, name: str, type: str = "persistent", **kwargs) -> "AgentBuilder":
        if self._memory is None:
            self._memory = MemoryConfig()
        self._memory.blocks.append(MemoryBlock(name=name, type=type, **kwargs))
        return self

    def guardrails(
        self,
        max_tokens_per_run: int | None = None,
        max_cost_usd_per_day: float | None = None,
        max_tool_calls_per_run: int | None = None,
    ) -> "AgentBuilder":
        self._guardrails = Guardrails(
            max_tokens_per_run=max_tokens_per_run,
            max_cost_usd_per_day=max_cost_usd_per_day,
            max_tool_calls_per_run=max_tool_calls_per_run,
        )
        return self

    def trace(self, backend: str = "langfuse") -> "AgentBuilder":
        self._observability = Observability(trace=backend)  # type: ignore
        return self

    # ---- Build ------------------------------------------------------------
    def build(self) -> AgentManifest:
        return AgentManifest(
            apiVersion="forgeos/v1",
            kind="Agent",
            metadata=Metadata(
                name=self._name,
                description=self._description,
                department=self._department,
                labels=self._labels,
            ),
            spec=Spec(
                stack=self._stack,  # type: ignore
                execution_type=self._execution_type,  # type: ignore
                ownership=self._ownership,  # type: ignore
                owner_id=self._owner_id,
                llm=LLMConfig(**self._llm),
                schedule=self._schedule,
                event_triggers=self._event_triggers,
                goal=self._goal,
                tools=self._tools,
                system_prompt=self._system_prompt,
                memory=self._memory,
                guardrails=self._guardrails,
                observability=self._observability,
                metadata=self._metadata,
            ),
        )


class Agent:
    """Base class for declarative agent definitions (CrewAI-style).

    Subclass and set class attributes, then call `.manifest()` to compile.
    """

    # Metadata
    name: str = ""
    description: str = ""
    department: str = ""

    # Spec
    stack: str = "forgeos"
    execution_type: str = "reflex"
    ownership: str = "shared"
    owner_id: str | None = None

    # LLM
    model: str = "claude-sonnet-4-5-20250514"
    provider: str = "anthropic"
    fallback_provider: str | None = None

    # Lifecycle
    schedule: str | None = None
    event_triggers: list[str] = []
    goal: str = ""

    # Capabilities
    tools: list[str] = []
    system_prompt: str = ""

    # Advanced
    memory_blocks: list[dict] = []
    max_tokens_per_run: int | None = None
    max_cost_usd_per_day: float | None = None
    metadata_extra: dict = {}

    @classmethod
    def builder(cls, name: str) -> AgentBuilder:
        """Start building a new agent manifest."""
        return AgentBuilder(name)

    @classmethod
    def manifest(cls) -> AgentManifest:
        """Compile the class attributes into an AgentManifest."""
        if not cls.name:
            raise ValueError(f"{cls.__name__}: 'name' class attribute is required")

        b = AgentBuilder(cls.name)
        b._description = cls.description
        b._department = cls.department
        b._stack = cls.stack
        b._execution_type = cls.execution_type
        b._ownership = cls.ownership
        b._owner_id = cls.owner_id
        b._llm = {"chat_model": cls.model, "provider": cls.provider}
        if cls.fallback_provider:
            b._llm["metadata"] = {"fallback_provider": cls.fallback_provider}
        b._schedule = cls.schedule
        b._event_triggers = list(cls.event_triggers)
        b._goal = cls.goal
        b._tools = list(cls.tools)
        if cls.system_prompt:
            b._system_prompt = cls.system_prompt
        if cls.memory_blocks:
            b._memory = MemoryConfig(blocks=[MemoryBlock(**mb) for mb in cls.memory_blocks])
        if cls.max_tokens_per_run or cls.max_cost_usd_per_day:
            b._guardrails = Guardrails(
                max_tokens_per_run=cls.max_tokens_per_run,
                max_cost_usd_per_day=cls.max_cost_usd_per_day,
            )
        b._metadata = dict(cls.metadata_extra)
        return b.build()
