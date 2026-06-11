"""Treasury agent configurations — minimal stub for platform boot.

Treasury agents are deployed as declarative manifests (see agents/*.yaml),
not via the legacy in-code registry. These symbols exist only to satisfy the
company-pack loader (src/config/agent_configs.py)."""
from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier  # noqa: F401

AGENT_DEFINITIONS: list[dict] = []
SUBAGENT_MAP: dict[str, list[str]] = {}
SYSTEM_PROMPTS: dict[str, str] = {}
TOOL_PERMISSIONS: dict[str, list[str]] = {}


def build_registry(company_name: str = "Treasury") -> AgentRegistry:
    return AgentRegistry()
