"""
Control-flow signals for the continuation runtime.

* :class:`Suspend` — a ``BaseException`` raised when an action cannot complete
  synchronously and the run must park (human approval, A2A await, external
  wait). It is a ``BaseException`` (not ``Exception``) so an over-broad
  ``except Exception`` inside a tool handler cannot swallow it.

* :class:`Resolution` — the payload delivered (by the A2H gateway, an A2A job
  completion, a webhook, or a timer) to wake a suspended continuation.

* :data:`SuspendReason` / :data:`ResolutionOutcome` — the small string enums
  that tie a suspension to the way it is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SuspendReason:
    """Why a continuation parked. Open taxonomy — string-valued.

    The reason also selects the process phase the engine parks in:
    ``human_*`` -> ``AWAITING_HUMAN``; everything else -> ``AWAITING_EXTERNAL``.
    """

    HUMAN_APPROVAL = "human_approval"   # kernel.syscall returned ask_human
    HUMAN_INPUT = "human_input"         # a tool needs a value from a human
    A2A_AWAIT = "a2a_await"             # waiting on another agent's async job
    EXTERNAL_WAIT = "external_wait"     # generic external wait (webhook/timer)

    HUMAN_REASONS = frozenset({HUMAN_APPROVAL, HUMAN_INPUT})


class ResolutionOutcome:
    """How a suspension was resolved."""

    ACCEPT = "accept"   # human approved -> execute the gated tool with a token
    REJECT = "reject"   # human rejected -> inject an error tool_result
    RESULT = "result"   # external result available -> inject it as the tool_result


@dataclass
class Suspend(BaseException):
    """Raised (or returned via the internal sentinel) when a tool call cannot
    complete synchronously and the run must park.

    Carries the ``ToolCallRecord`` that triggered the park and the
    ``external_ref`` (A2H request id / A2A job id / opaque token) the resume
    path will use to find this continuation again.
    """

    reason: str
    tool_use_id: str
    external_ref: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"Suspend(reason={self.reason!r}, tool_use_id={self.tool_use_id!r}, ref={self.external_ref!r})"


@dataclass
class Resolution:
    """Delivered to :meth:`StepEngine.resume` to wake a suspended continuation.

    * ``continuation_id`` — which continuation to resume.
    * ``tool_use_id``     — which pending tool call this resolves.
    * ``outcome``         — one of :class:`ResolutionOutcome`.
    * ``capability_token``— for ``accept``: the token that flips the kernel's
                            capability stage from ``ask_human`` to ``allow``.
    * ``result_payload``  — for ``result``: the value to inject as the tool
                            result (A2A job output, external value).
    * ``responded_by``    — audit: who resolved it.
    """

    continuation_id: str
    tool_use_id: str
    outcome: str = ResolutionOutcome.ACCEPT
    capability_token: str | None = None
    result_payload: Any = None
    responded_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "continuation_id": self.continuation_id,
            "tool_use_id": self.tool_use_id,
            "outcome": self.outcome,
            "capability_token": self.capability_token,
            "result_payload": self.result_payload,
            "responded_by": self.responded_by,
        }


__all__ = [
    "Resolution",
    "ResolutionOutcome",
    "Suspend",
    "SuspendReason",
]
