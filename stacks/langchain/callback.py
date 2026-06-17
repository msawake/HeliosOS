# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Helios OS kernel callback for LangChain / LangGraph agents.

ONE callback handler that gates ALL tool calls via the Helios OS kernel.
Works with any LangChain agent (AgentExecutor, create_tool_calling_agent)
and any LangGraph workflow (ToolNode, create_react_agent).

Two modes:
  Mode A (in-process): kernel is in the same process → ~0.1ms per check
  Mode C (HTTP remote): kernel on separate Cloud Run → ~50ms per check

Usage — add Helios OS governance to ANY LangChain agent with one line::

    from stacks.langchain.callback import ForgeOSKernelCallback

    callback = ForgeOSKernelCallback(
        forgeos_url="https://forgeos-api.example.com",
        agent_id="my-agent",
    )

    # LangChain AgentExecutor:
    result = executor.invoke(
        {"input": "your prompt"},
        config={"callbacks": [callback]},
    )

    # LangGraph create_react_agent:
    result = agent.invoke(
        {"messages": [("user", "your prompt")]},
        config={"callbacks": [callback]},
    )

How it works:
  LangChain fires on_tool_start() BEFORE every tool execution.
  If raise_error=True and the callback raises, execution is blocked.
  The LLM sees a ToolException and adapts naturally.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Detect LangChain availability
try:
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tools import ToolException
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    BaseCallbackHandler = object  # type: ignore[assignment,misc]
    ToolException = Exception  # type: ignore[assignment,misc]


class ForgeOSKernelCallback(BaseCallbackHandler):
    """LangChain callback that checks Helios OS kernel before every tool call.

    Attributes:
        raise_error: Must be True for exceptions to propagate and block tools.
        forgeos_url: Base URL of the Helios OS control plane API.
        agent_id: The agent's registered ID in Helios OS.
        api_key: Optional API key for authentication.
    """

    raise_error: bool = True

    def __init__(
        self,
        forgeos_url: str = "",
        agent_id: str = "",
        api_key: str | None = None,
        *,
        kernel: Any = None,
    ):
        """Initialize the kernel callback.

        Args:
            forgeos_url: Helios OS API URL (Mode C — HTTP remote).
            agent_id: Agent ID registered in Helios OS.
            api_key: Optional API key for Helios OS.
            kernel: Direct kernel reference (Mode A — in-process).
                    If provided, HTTP is skipped.
        """
        super().__init__()
        self.forgeos_url = forgeos_url.rstrip("/") if forgeos_url else ""
        self.agent_id = agent_id
        self.api_key = api_key
        self._kernel = kernel

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Check Helios OS kernel before every tool call.

        Raises ToolException if the kernel denies or rate-limits the call.
        The LLM sees this as a tool error and adapts naturally.
        """
        tool_name = serialized.get("name", "")
        tool_input = inputs or {}

        try:
            decision = self._check_kernel(tool_name, tool_input)
        except Exception as e:
            logger.debug("Helios OS kernel check failed for %s: %s (allowing)", tool_name, e)
            return

        action = decision.get("action", "allow") if isinstance(decision, dict) else getattr(decision, "action", "allow")

        if action == "deny":
            reason = decision.get("reason", "policy violation") if isinstance(decision, dict) else getattr(decision, "reason", "")
            logger.info("Helios OS DENIED tool %s for agent %s: %s", tool_name, self.agent_id, reason)
            raise ToolException(f"Helios OS denied: {reason}")

        if action == "rate_limit":
            reason = decision.get("reason", "budget exceeded") if isinstance(decision, dict) else getattr(decision, "reason", "")
            logger.info("Helios OS RATE LIMITED tool %s for agent %s: %s", tool_name, self.agent_id, reason)
            raise ToolException(f"Helios OS rate limited: {reason}")

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record tool completion in audit trail (optional)."""
        pass

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Record tool error in audit trail (optional)."""
        pass

    def _check_kernel(self, tool_name: str, tool_input: dict) -> dict:
        """Check the kernel — in-process or HTTP."""
        # Mode A: in-process kernel (direct call)
        if self._kernel is not None:
            d = self._kernel.check_tool_call(self.agent_id, tool_name, tool_input)
            return d.to_dict() if hasattr(d, "to_dict") else d

        # Mode A: try Helios OS runtime (if we're inside the bootstrap)
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered and _rt.is_bound:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    decision = loop.run_until_complete(_rt.check_tool(tool_name, tool_input))
                    return decision.to_dict() if hasattr(decision, "to_dict") else {"action": "allow"}
                finally:
                    loop.close()
        except (ImportError, RuntimeError):
            pass

        # Mode C: HTTP remote kernel
        if not self.forgeos_url:
            return {"action": "allow", "reason": "no kernel configured"}

        import httpx
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        resp = httpx.post(
            f"{self.forgeos_url}/api/platform/kernel/check-tool",
            json={
                "agent_id": self.agent_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
