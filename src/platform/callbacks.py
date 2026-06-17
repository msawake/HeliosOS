"""
Typed callback system for Helios OS.

Provides interception points at three levels (agent, model, tool) with
approve/deny/modify/defer semantics. Replaces the deprecated legacy
hook chain in src/core/hooks.py.

Callbacks are registered with a priority (lower runs first) and optional
agent/namespace filters. On dispatch, all matching callbacks run in
priority order. First non-APPROVE result wins:
  deny > defer > guide > modify > approve
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class CallbackLevel(str, Enum):
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"


class CallbackTiming(str, Enum):
    BEFORE = "before"
    AFTER = "after"


class CallbackDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    MODIFY = "modify"
    DEFER = "defer"
    GUIDE = "guide"


@dataclass
class CallbackContext:
    """Passed to every callback."""
    agent_id: str
    namespace: str
    level: CallbackLevel
    timing: CallbackTiming
    event_name: str
    args: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallbackResult:
    decision: CallbackDecision = CallbackDecision.APPROVE
    reason: str = ""
    modified_args: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def approve(cls, reason: str = "") -> CallbackResult:
        return cls(decision=CallbackDecision.APPROVE, reason=reason)

    @classmethod
    def deny(cls, reason: str = "") -> CallbackResult:
        return cls(decision=CallbackDecision.DENY, reason=reason)

    @classmethod
    def modify(cls, modified_args: dict[str, Any], reason: str = "") -> CallbackResult:
        return cls(decision=CallbackDecision.MODIFY, modified_args=modified_args, reason=reason)

    @classmethod
    def defer(cls, reason: str = "") -> CallbackResult:
        return cls(decision=CallbackDecision.DEFER, reason=reason)

    @classmethod
    def guide(cls, reason: str = "") -> CallbackResult:
        return cls(decision=CallbackDecision.GUIDE, reason=reason)


CallbackFn = Callable[[CallbackContext], CallbackResult | Awaitable[CallbackResult]]


@dataclass
class _Registration:
    id: str
    level: CallbackLevel
    timing: CallbackTiming
    event_name: str
    callback: CallbackFn
    priority: int
    agent_filter: str | None
    namespace_filter: str | None


class CallbackRegistry:
    """Central registry for typed callbacks."""

    def __init__(self):
        self._registrations: list[_Registration] = []

    def register(
        self,
        level: CallbackLevel,
        timing: CallbackTiming,
        event_name: str,
        callback: CallbackFn,
        priority: int = 100,
        agent_filter: str | None = None,
        namespace_filter: str | None = None,
    ) -> str:
        """Register a callback. Returns a registration ID."""
        reg_id = str(uuid.uuid4())[:8]
        self._registrations.append(_Registration(
            id=reg_id,
            level=level,
            timing=timing,
            event_name=event_name,
            callback=callback,
            priority=priority,
            agent_filter=agent_filter,
            namespace_filter=namespace_filter,
        ))
        self._registrations.sort(key=lambda r: r.priority)
        return reg_id

    def unregister(self, reg_id: str) -> bool:
        """Remove a callback by registration ID."""
        before = len(self._registrations)
        self._registrations = [r for r in self._registrations if r.id != reg_id]
        return len(self._registrations) < before

    async def dispatch(self, context: CallbackContext) -> CallbackResult:
        """Run all matching callbacks in priority order.

        Resolution order: deny > defer > modify > approve.
        First non-APPROVE result of the highest precedence wins.
        """
        matching = self._match(context)
        if not matching:
            return CallbackResult.approve()

        best_result = CallbackResult.approve()
        precedence = {
            CallbackDecision.DENY: 0,
            CallbackDecision.DEFER: 1,
            CallbackDecision.GUIDE: 2,
            CallbackDecision.MODIFY: 3,
            CallbackDecision.APPROVE: 4,
        }

        for reg in matching:
            try:
                result = reg.callback(context)
                if inspect.isawaitable(result):
                    result = await result
                if not isinstance(result, CallbackResult):
                    continue
                if precedence[result.decision] <= precedence[best_result.decision]:
                    best_result = result
                if result.decision == CallbackDecision.DENY:
                    break  # deny is final
            except Exception as e:
                logger.error("Callback %s raised: %s", reg.id, e)
                continue

        return best_result

    def _match(self, context: CallbackContext) -> list[_Registration]:
        """Find all registrations matching the context."""
        matched = []
        for reg in self._registrations:
            if reg.level != context.level:
                continue
            if reg.timing != context.timing:
                continue
            if reg.event_name != "*" and reg.event_name != context.event_name:
                continue
            if reg.agent_filter and reg.agent_filter != context.agent_id:
                continue
            if reg.namespace_filter and reg.namespace_filter != context.namespace:
                continue
            matched.append(reg)
        return matched

    def count(self) -> int:
        return len(self._registrations)

    def clear(self) -> None:
        self._registrations.clear()
