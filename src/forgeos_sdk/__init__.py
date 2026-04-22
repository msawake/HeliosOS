"""
ForgeOS Python SDK.

Declare, deploy, and manage ForgeOS agents from Python.

Quickstart:
    from forgeos_sdk import Agent, ForgeOSClient

    # Build a manifest
    manifest = (Agent.builder("email-checker")
        .forgeos()
        .scheduled("0 7,12,17 * * *")
        .model("gpt-4o", provider="openai")
        .tools("mcp__filesystem__*", "company__publish_event")
        .prompt("You check email and summarize...")
        .build())

    # Deploy it
    with ForgeOSClient() as client:
        agent_id = client.deploy(manifest)
        print(f"Deployed: {agent_id}")
"""

from .agent import Agent, AgentBuilder
from .client import ForgeOSClient, ForgeOSError
from .kernel import Kernel, KernelDecision
from .runtime import runtime, Runtime, BudgetSnapshot, ProcessSnapshot, CheckpointData, CapabilityToken
from .manifest import (
    # v1 primitives
    AgentManifest,
    Guardrails,
    LLMConfig,
    MemoryBlock,
    MemoryConfig,
    Metadata,
    Observability,
    Spec,
    SystemPrompt,
    # v2 AgentOS primitives
    A2AConfig,
    A2APeer,
    AgentCondition,
    AgentDependency,
    AgentStatus,
    Boundaries,
    Budgets,
    Capabilities,
    DataBoundaries,
    Dependencies,
    Governance,
    HITLApproval,
    Lifecycle,
    PolicyRef,
    Runtime,
    ToolACL,
    Trigger,
)

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "AgentBuilder",
    "AgentManifest",
    "ForgeOSClient",
    "ForgeOSError",
    "Kernel",
    "KernelDecision",
    "runtime",
    "Runtime",
    "BudgetSnapshot",
    "ProcessSnapshot",
    "CheckpointData",
    "CapabilityToken",
    "Guardrails",
    "LLMConfig",
    "MemoryBlock",
    "MemoryConfig",
    "Metadata",
    "Observability",
    "Spec",
    "SystemPrompt",
    # v2 AgentOS primitives
    "A2AConfig",
    "A2APeer",
    "AgentCondition",
    "AgentDependency",
    "AgentStatus",
    "Boundaries",
    "Budgets",
    "Capabilities",
    "DataBoundaries",
    "Dependencies",
    "Governance",
    "HITLApproval",
    "Lifecycle",
    "PolicyRef",
    "Runtime",
    "ToolACL",
    "Trigger",
]
