# SPDX-License-Identifier: Apache-2.0
"""Permissive syscall stub — pipeline disabled, all operations pass through."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from src.platform.kernel_stubs._facade_stub import KernelDecision


@dataclass
class Syscall:
    verb: str
    subject: str
    object: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    budget_ticket: str | None = None
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class Stage(Protocol):
    def __call__(self, syscall: Syscall) -> KernelDecision | None: ...


STAGE_ORDER: tuple[str, ...] = (
    "identity", "capability", "quota", "policy", "boundary", "dispatch", "audit",
)

FEATURE_FLAG_ENV = "FORGEOS_SYSCALL_PIPELINE"


class SyscallPipeline:
    def __init__(self, stages: dict[str, Stage | None] | None = None) -> None:
        self._stages = stages or {}

    def set_stage(self, name: str, stage: Stage | None) -> None:
        self._stages[name] = stage

    def run(self, syscall: Syscall) -> KernelDecision:
        return KernelDecision.allow(reason="community edition — no pipeline")


def syscall_pipeline_enabled() -> bool:
    return False


def make_capability_stage(pm: Any) -> Stage:
    return lambda sc: None  # type: ignore[return-value]

def make_quota_stage(bm: Any) -> Stage:
    return lambda sc: None  # type: ignore[return-value]

def make_policy_stage(pe: Any) -> Stage:
    return lambda sc: None  # type: ignore[return-value]

def make_boundary_stage(dbm: Any) -> Stage:
    return lambda sc: None  # type: ignore[return-value]

def make_dispatch_stage(d: Callable[[Syscall], KernelDecision | None] | None) -> Stage:
    return lambda sc: None  # type: ignore[return-value]

def make_audit_stage(ar: Any) -> Stage:
    return lambda sc: None  # type: ignore[return-value]
