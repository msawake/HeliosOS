"""LeadForge agent configurations — minimal stub for platform boot."""
from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier

AGENT_DEFINITIONS: list[dict] = []
SUBAGENT_MAP: dict[str, list[str]] = {}
SYSTEM_PROMPTS: dict[str, str] = {}
TOOL_PERMISSIONS: dict[str, list[str]] = {}


def build_registry() -> AgentRegistry:
    return AgentRegistry()
