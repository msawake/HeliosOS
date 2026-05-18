# SPDX-License-Identifier: Apache-2.0
"""
Progressive rollout — canary deploys for agent version updates.

Instead of instantly replacing an agent, deploy a canary alongside the
existing version. Route a percentage of traffic to the canary. Monitor
health. Auto-promote if healthy, auto-rollback if not.

Usage in manifest:
  spec:
    lifecycle:
      rollout:
        type: canary
        canary_percent: 10
        canary_duration_hours: 24
        auto_promote: true
        auto_rollback: true
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RolloutState(str, Enum):
    PENDING = "pending"
    CANARY_ACTIVE = "canary_active"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class RolloutStrategy:
    type: str = "immediate"
    canary_percent: int = 10
    canary_duration_hours: float = 24.0
    success_threshold: float = 0.95
    auto_promote: bool = True
    auto_rollback: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RolloutStrategy":
        if not data:
            return cls()
        return cls(
            type=data.get("type", "immediate"),
            canary_percent=data.get("canary_percent", 10),
            canary_duration_hours=data.get("canary_duration_hours", 24.0),
            success_threshold=data.get("success_threshold", 0.95),
            auto_promote=data.get("auto_promote", True),
            auto_rollback=data.get("auto_rollback", True),
        )


@dataclass
class Rollout:
    rollout_id: str
    agent_name: str
    namespace: str
    strategy: RolloutStrategy
    state: RolloutState = RolloutState.PENDING
    original_agent_id: str | None = None
    canary_agent_id: str | None = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    canary_invocations: int = 0
    canary_errors: int = 0
    original_invocations: int = 0
    original_errors: int = 0

    @property
    def canary_error_rate(self) -> float:
        if self.canary_invocations == 0:
            return 0.0
        return self.canary_errors / self.canary_invocations

    @property
    def canary_success_rate(self) -> float:
        return 1.0 - self.canary_error_rate

    @property
    def duration_elapsed(self) -> timedelta:
        start = datetime.fromisoformat(self.started_at)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - start

    @property
    def duration_target(self) -> timedelta:
        return timedelta(hours=self.strategy.canary_duration_hours)

    @property
    def is_duration_complete(self) -> bool:
        return self.duration_elapsed >= self.duration_target

    def should_promote(self) -> bool:
        return (
            self.is_duration_complete
            and self.canary_success_rate >= self.strategy.success_threshold
            and self.canary_invocations > 0
            and self.strategy.auto_promote
        )

    def should_rollback(self) -> bool:
        if self.canary_invocations < 5:
            return False
        return (
            self.canary_error_rate > (1.0 - self.strategy.success_threshold)
            and self.strategy.auto_rollback
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["canary_error_rate"] = self.canary_error_rate
        d["canary_success_rate"] = self.canary_success_rate
        d["duration_elapsed_seconds"] = self.duration_elapsed.total_seconds()
        d["duration_target_seconds"] = self.duration_target.total_seconds()
        return d


def route_to_canary(session_id: str, canary_percent: int) -> bool:
    """Deterministic routing based on session_id hash."""
    h = int(hashlib.md5(session_id.encode()).hexdigest()[:8], 16)
    return (h % 100) < canary_percent


class RolloutManager:
    """Manages progressive rollouts for agent updates."""

    def __init__(self, executor=None):
        self._executor = executor
        self._rollouts: dict[str, Rollout] = {}
        self._active_by_agent: dict[str, str] = {}

    def get_active_rollout(self, agent_name: str, namespace: str = "default") -> Rollout | None:
        key = f"{namespace}/{agent_name}"
        rollout_id = self._active_by_agent.get(key)
        if rollout_id:
            return self._rollouts.get(rollout_id)
        return None

    async def start_canary(
        self,
        agent_name: str,
        namespace: str,
        strategy: RolloutStrategy,
        original_agent_id: str,
        canary_agent_id: str,
    ) -> Rollout:
        rollout_id = f"rollout-{agent_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        rollout = Rollout(
            rollout_id=rollout_id,
            agent_name=agent_name,
            namespace=namespace,
            strategy=strategy,
            state=RolloutState.CANARY_ACTIVE,
            original_agent_id=original_agent_id,
            canary_agent_id=canary_agent_id,
        )
        self._rollouts[rollout_id] = rollout
        self._active_by_agent[f"{namespace}/{agent_name}"] = rollout_id
        logger.info(
            "Canary started: %s (original=%s, canary=%s, %d%%)",
            rollout_id, original_agent_id, canary_agent_id,
            strategy.canary_percent,
        )
        return rollout

    def record_invocation(self, rollout_id: str, is_canary: bool, success: bool) -> None:
        rollout = self._rollouts.get(rollout_id)
        if not rollout:
            return
        if is_canary:
            rollout.canary_invocations += 1
            if not success:
                rollout.canary_errors += 1
        else:
            rollout.original_invocations += 1
            if not success:
                rollout.original_errors += 1

    async def check_and_decide(self, rollout_id: str) -> str:
        """Check rollout health and decide: continue, promote, or rollback."""
        rollout = self._rollouts.get(rollout_id)
        if not rollout or rollout.state != RolloutState.CANARY_ACTIVE:
            return "no_action"

        if rollout.should_rollback():
            await self.rollback(rollout_id)
            return "rolled_back"

        if rollout.should_promote():
            await self.promote(rollout_id)
            return "promoted"

        return "continue"

    async def promote(self, rollout_id: str) -> None:
        rollout = self._rollouts.get(rollout_id)
        if not rollout:
            return
        rollout.state = RolloutState.PROMOTED
        rollout.completed_at = datetime.now(timezone.utc).isoformat()
        key = f"{rollout.namespace}/{rollout.agent_name}"
        self._active_by_agent.pop(key, None)
        logger.info(
            "Canary PROMOTED: %s (success_rate=%.1f%%, invocations=%d)",
            rollout_id, rollout.canary_success_rate * 100,
            rollout.canary_invocations,
        )

    async def rollback(self, rollout_id: str) -> None:
        rollout = self._rollouts.get(rollout_id)
        if not rollout:
            return
        rollout.state = RolloutState.ROLLED_BACK
        rollout.completed_at = datetime.now(timezone.utc).isoformat()
        key = f"{rollout.namespace}/{rollout.agent_name}"
        self._active_by_agent.pop(key, None)
        logger.info(
            "Canary ROLLED BACK: %s (error_rate=%.1f%%, invocations=%d)",
            rollout_id, rollout.canary_error_rate * 100,
            rollout.canary_invocations,
        )

    def list_rollouts(self, state: RolloutState | None = None) -> list[Rollout]:
        if state:
            return [r for r in self._rollouts.values() if r.state == state]
        return list(self._rollouts.values())

    def get(self, rollout_id: str) -> Rollout | None:
        return self._rollouts.get(rollout_id)
