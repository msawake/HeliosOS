"""
Governance hook chain for the AI company agent system.

DEPRECATED — SUPERSEDED BY ``src/platform/syscall.py``.

New code MUST NOT import from this module. The syscall pipeline
(``kernel.syscall("tool.call", ...)``) is the single admission path for
tool calls, A2A invocations, secret fetches, and budget reservations.
Enable it with ``FORGEOS_SYSCALL_PIPELINE=1`` (Phase A #1).

Blockers on full deletion (need migration first):
    * ``src/core/claude_client.py`` — imports ``HookDecision``.
    * ``src/core/redis_rate_limiter.py`` — reuses ``AgentContext`` /
      ``HookDecision`` / ``HookResult`` as its interface types.
    * ``src/core/agent_invoker.py`` — legacy 3-tier orchestrator.
    * ``src/bootstrap.py`` — ``create_hook_chain`` at boot.

Until those move to syscall types (``KernelDecision`` + syscall context),
this module keeps the six hooks:

1. audit_logger    - Immutable record of every agent action
2. rate_limiter    - Prevents runaway loops and API abuse
3. auth_check      - Enforces tool-level permissions per agent role
4. cost_tracker    - Monitors and enforces token/cost budgets
5. compliance_checker - Validates outputs before external actions
6. slack_notifier  - Alerts humans on critical events
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

warnings.warn(
    "src.core.hooks is deprecated; use src.platform.syscall (set "
    "FORGEOS_SYSCALL_PIPELINE=1). Tracked for deletion after migration of "
    "claude_client, redis_rate_limiter, agent_invoker, bootstrap.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


class HookDecision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ASK_HUMAN = "ask_human"


@dataclass
class HookResult:
    decision: HookDecision
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    agent_id: str
    agent_type: str  # "orchestrator" | "doer"
    department: str
    tier: int  # 0=human, 1=executive, 2=dept-lead, 3=worker
    session_id: str
    allowed_tools: list[str]
    budget_tokens: int
    model: str


# ---------------------------------------------------------------------------
# 1. Audit Logger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only audit log for every agent action. Writes to PostgreSQL."""

    def __init__(self, db_writer=None):
        self._db_writer = db_writer
        self._buffer: list[dict] = []

    def log(
        self,
        context: AgentContext,
        hook_event: str,
        tool_name: str | None,
        tool_input: dict | None,
        tool_output: dict | None = None,
        decision: str | None = None,
        reasoning: str | None = None,
    ) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": context.agent_id,
            "agent_type": context.agent_type,
            "department": context.department,
            "tier": context.tier,
            "session_id": context.session_id,
            "hook_event": hook_event,
            "tool_name": tool_name,
            "tool_input_hash": (
                hashlib.sha256(
                    json.dumps(tool_input, sort_keys=True, default=str).encode()
                ).hexdigest()
                if tool_input
                else None
            ),
            "decision": decision,
            "reasoning": reasoning,
            "model": context.model,
        }

        self._buffer.append(entry)
        if self._db_writer:
            self._db_writer.write_audit_entry(entry)

        logger.info(
            "AUDIT | %s | %s | %s | %s | %s",
            context.agent_id,
            hook_event,
            tool_name,
            decision or "n/a",
            reasoning or "",
        )
        return entry

    def flush(self):
        if self._db_writer and self._buffer:
            self._db_writer.write_audit_batch(self._buffer)
            self._buffer.clear()

    def get_buffer(self) -> list[dict]:
        return list(self._buffer)


# ---------------------------------------------------------------------------
# 2. Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Enforces per-agent and per-minute rate limits on tool calls.
    Prevents runaway agent loops.

    Uses agent_id (not session_id) for tracking, because session_id is
    a fresh UUID per invocation — limits would never trigger otherwise.
    """

    def __init__(
        self,
        max_calls_per_session: int = 100,
        max_calls_per_minute: int = 30,
    ):
        self.max_per_session = max_calls_per_session
        self.max_per_minute = max_calls_per_minute
        self._agent_counts: dict[str, int] = {}
        self._minute_windows: dict[str, list[float]] = {}
        self._check_count = 0

    # Keep old attribute names accessible for backward compatibility with tests
    @property
    def _session_counts(self) -> dict[str, int]:
        return self._agent_counts

    def check(self, context: AgentContext) -> HookResult:
        session_key = f"session:{context.session_id}" if context.session_id else f"agent:{context.agent_id}"
        agent_key = f"agent:{context.agent_id}"

        # Cleanup when dict grows too large (prevents unbounded memory growth)
        self._check_count += 1
        if len(self._agent_counts) > 10000 or self._check_count % 100 == 0:
            self._cleanup_stale_agents()

        # Per-session total count
        self._agent_counts.setdefault(session_key, 0)
        self._agent_counts[session_key] += 1
        if self._agent_counts[session_key] > self.max_per_session:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session {session_key} exceeded {self.max_per_session} tool calls",
                metadata={"count": self._agent_counts[session_key]},
            )

        # Per-minute sliding window (per agent to prevent abuse across sessions)
        now = time.time()
        self._minute_windows.setdefault(agent_key, [])
        window = self._minute_windows[agent_key]
        window.append(now)
        # Trim to last 60 seconds
        self._minute_windows[agent_key] = [t for t in window if now - t < 60]
        if len(self._minute_windows[agent_key]) > self.max_per_minute:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Agent {agent_key} exceeded {self.max_per_minute} calls/minute",
                metadata={"calls_in_window": len(self._minute_windows[agent_key])},
            )

        return HookResult(decision=HookDecision.ALLOW)

    def reset_session(self, session_id: str):
        """Reset rate limits. Accepts session_id for backward compat but also works as agent_id."""
        session_key = f"session:{session_id}"
        agent_key = f"agent:{session_id}"
        
        # Try both formats for backward compatibility
        self._agent_counts.pop(session_key, None)
        self._agent_counts.pop(session_id, None)
        
        self._minute_windows.pop(agent_key, None)
        self._minute_windows.pop(session_id, None)

    def reset_agent(self, agent_id: str):
        """Reset rate limits for a specific agent."""
        agent_key = f"agent:{agent_id}"
        
        self._agent_counts.pop(agent_key, None)
        self._agent_counts.pop(agent_id, None)
        
        self._minute_windows.pop(agent_key, None)
        self._minute_windows.pop(agent_id, None)

    def _cleanup_stale_agents(self):
        """Remove agents with no activity in the last hour to prevent memory leaks."""
        now = time.time()
        stale = [
            key for key, timestamps in self._minute_windows.items()
            if not timestamps or now - timestamps[-1] > 3600
        ]
        for key in stale:
            self._agent_counts.pop(key, None)
            self._minute_windows.pop(key, None)
        if stale:
            logger.debug("RateLimiter: cleaned up %d stale agent entries", len(stale))


# ---------------------------------------------------------------------------
# 3. Auth Check (Tool-level permissions)
# ---------------------------------------------------------------------------

# Dangerous command patterns blocked for all agents
BLOCKED_BASH_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\.",
    r"DROP\s+TABLE",
    r"DROP\s+DATABASE",
    r"TRUNCATE\s+TABLE",
    r"curl\s+.*\|\s*sh",
    r"curl\s+.*\|\s*bash",
    r"wget\s+.*\|\s*sh",
    r"mkfs\.",
    r"dd\s+if=",
    r":(){ :\|:& };:",
    r"chmod\s+-R\s+777\s+/",
    r">\s*/dev/sd",
    r"shutdown",
    r"reboot",
    r"init\s+0",
]

# Tools that require special authorization
SENSITIVE_TOOLS = {
    "send_gmail_message": {"requires_compliance_check": True},
    "send_message": {"requires_compliance_check": True},
    "create_event": {"max_tier": 2},
    "share_drive_file": {"requires_audit": True},
    "set_drive_file_permissions": {"requires_audit": True},
    "transfer_drive_ownership": {"requires_hitl": True},
}


class AuthChecker:
    """
    Enforces tool-level permissions based on agent role and tier.
    Implements principle of least privilege.
    """

    def check(
        self,
        context: AgentContext,
        tool_name: str,
        tool_input: dict | None = None,
    ) -> HookResult:
        # Check if tool is in agent's allowed list
        if tool_name not in context.allowed_tools:
            # Check MCP tool prefixes
            tool_allowed = False
            for allowed in context.allowed_tools:
                if allowed.endswith("*") and tool_name.startswith(allowed[:-1]):
                    tool_allowed = True
                    break
                if tool_name == allowed:
                    tool_allowed = True
                    break
            if not tool_allowed:
                return HookResult(
                    decision=HookDecision.BLOCK,
                    reason=f"Agent {context.agent_id} not authorized for tool {tool_name}",
                )

        # Check Bash command safety
        if tool_name == "Bash" and tool_input:
            command = tool_input.get("command", "")
            for pattern in BLOCKED_BASH_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    return HookResult(
                        decision=HookDecision.BLOCK,
                        reason=f"Blocked dangerous command pattern: {pattern}",
                        metadata={"command": command[:200]},
                    )

        # Check sensitive tool restrictions
        base_tool = tool_name.split("__")[-1] if "__" in tool_name else tool_name
        if base_tool in SENSITIVE_TOOLS:
            restrictions = SENSITIVE_TOOLS[base_tool]
            if restrictions.get("requires_hitl"):
                return HookResult(
                    decision=HookDecision.ASK_HUMAN,
                    reason=f"Tool {tool_name} requires human approval",
                )
            max_tier = restrictions.get("max_tier")
            if max_tier is not None and context.tier > max_tier:
                return HookResult(
                    decision=HookDecision.BLOCK,
                    reason=f"Tool {tool_name} restricted to tier {max_tier}+, agent is tier {context.tier}",
                )

        # Doer agents (tier 3) cannot use the Agent tool
        if tool_name == "Agent" and context.tier >= 3:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason="Doer agents (tier 3) cannot spawn sub-agents",
            )

        return HookResult(decision=HookDecision.ALLOW)


# ---------------------------------------------------------------------------
# 4. Cost Tracker
# ---------------------------------------------------------------------------

class CostTracker:
    """
    Tracks token consumption and USD cost per session and globally.
    Enforces budget limits.

    Thread-safe: uses a lock to prevent race conditions between
    pre_check() (which reserves estimated cost) and track() (which
    adjusts reservation to actual cost).
    """

    # Import shared pricing registry (supports Claude + OpenAI + custom models)
    from src.core.model_client import MODEL_PRICING as PRICING

    def __init__(self, per_session_limit_usd: float = 50.0):
        self.per_session_limit = per_session_limit_usd
        self._session_costs: dict[str, float] = {}
        self._session_tokens: dict[str, dict[str, int]] = {}
        self._global_daily_tokens: int = 0
        self._daily_reset_date: str = ""
        self._lock = threading.Lock()

    def pre_check(self, context: AgentContext, estimated_tokens: int = 10000) -> HookResult:
        """Check if estimated cost would exceed session budget BEFORE the API call.

        Called in pre_tool_use to prevent overspend. Uses conservative estimate
        of tokens that will be consumed by the next API call.

        RESERVES the estimated cost immediately under a lock so that concurrent
        calls cannot slip through the budget check.
        """
        from src.core.model_client import estimate_cost as _estimate_cost

        sid = context.session_id
        # Conservative estimate: assume output tokens = input tokens (not input/2)
        estimated_cost = _estimate_cost(context.model, estimated_tokens, estimated_tokens)

        with self._lock:
            current_cost = self._session_costs.get(sid, 0.0)

            if current_cost + estimated_cost > self.per_session_limit:
                return HookResult(
                    decision=HookDecision.BLOCK,
                    reason=(
                        f"Session cost ${current_cost:.2f} + estimated ${estimated_cost:.2f} "
                        f"would exceed limit ${self.per_session_limit:.2f}"
                    ),
                    metadata={
                        "current_cost": current_cost,
                        "estimated_cost": estimated_cost,
                        "limit": self.per_session_limit,
                    },
                )

            # RESERVE the estimated cost immediately to prevent concurrent overspend
            self._session_costs[sid] = current_cost + estimated_cost

        return HookResult(
            decision=HookDecision.ALLOW,
            metadata={"reserved": estimated_cost},
        )

    def track(
        self,
        context: AgentContext,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reserved: float = 0.0,
    ) -> HookResult:
        sid = context.session_id
        model = context.model

        # Calculate actual cost (uses shared pricing registry for all providers)
        from src.core.model_client import estimate_cost as _estimate_cost
        actual_cost = _estimate_cost(model, input_tokens, output_tokens)

        with self._lock:
            # Adjust: remove reservation, add actual cost
            current = self._session_costs.get(sid, 0.0)
            self._session_costs[sid] = current - reserved + actual_cost

            # Track session tokens
            self._session_tokens.setdefault(sid, {"input": 0, "output": 0})
            self._session_tokens[sid]["input"] += input_tokens
            self._session_tokens[sid]["output"] += output_tokens

            # Track global daily tokens
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._daily_reset_date:
                self._global_daily_tokens = 0
                self._daily_reset_date = today
            self._global_daily_tokens += input_tokens + output_tokens

            session_cost = self._session_costs[sid]
            session_tokens = dict(self._session_tokens[sid])
            daily_tokens = self._global_daily_tokens

        # Check session limit
        if session_cost > self.per_session_limit:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session cost ${session_cost:.2f} exceeds limit ${self.per_session_limit:.2f}",
                metadata={
                    "session_cost": session_cost,
                    "limit": self.per_session_limit,
                },
            )

        return HookResult(
            decision=HookDecision.ALLOW,
            metadata={
                "session_cost": session_cost,
                "session_tokens": session_tokens,
                "global_daily_tokens": daily_tokens,
            },
        )

    def get_session_cost(self, session_id: str) -> float:
        return self._session_costs.get(session_id, 0.0)

    def get_daily_tokens(self) -> int:
        return self._global_daily_tokens

    def get_session_tokens(self, session_id: str) -> dict[str, int]:
        return self._session_tokens.get(session_id, {"input": 0, "output": 0})


# ---------------------------------------------------------------------------
# 5. Compliance Checker
# ---------------------------------------------------------------------------

# Patterns that should never appear in external communications
COMPLIANCE_BLOCKLIST = [
    r"(?i)guarantee[sd]?\s+(return|profit|result)",
    r"(?i)we\s+(?:will|shall)\s+never\s+(?:fail|lose|miss)",
    r"(?i)(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S+",
    r"(?i)social\s*security\s*(?:number)?",
    r"(?i)credit\s*card\s*(?:number)?",
]

# Required disclaimers for certain content types
REQUIRED_DISCLAIMERS = {
    "financial_advice": "This is not financial advice.",
    "legal_opinion": "This does not constitute legal advice. Consult a licensed attorney.",
    "medical": "This is not medical advice. Consult a healthcare professional.",
}


class ComplianceChecker:
    """
    Validates agent outputs before external-facing actions.
    Checks for PII, prohibited claims, and required disclaimers.
    """

    def check_content(
        self,
        content: str,
        content_type: str = "general",
    ) -> HookResult:
        issues: list[str] = []

        # Check for blocklisted patterns
        for pattern in COMPLIANCE_BLOCKLIST:
            if re.search(pattern, content):
                issues.append(f"Content matches prohibited pattern: {pattern}")

        # Check for required disclaimers
        if content_type in REQUIRED_DISCLAIMERS:
            disclaimer = REQUIRED_DISCLAIMERS[content_type]
            if disclaimer.lower() not in content.lower():
                issues.append(f"Missing required disclaimer for {content_type}")

        if issues:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Compliance check failed: {'; '.join(issues)}",
                metadata={"issues": issues},
            )

        return HookResult(decision=HookDecision.ALLOW)

    def check_email(self, to: str, subject: str, body: str) -> HookResult:
        """Special compliance check for outbound emails."""
        # Check body content
        body_result = self.check_content(body)
        if body_result.decision != HookDecision.ALLOW:
            return body_result

        # Ensure no mass mailing (basic check)
        recipients = [r.strip() for r in to.split(",")]
        if len(recipients) > 50:
            return HookResult(
                decision=HookDecision.ASK_HUMAN,
                reason=f"Mass email to {len(recipients)} recipients requires approval",
            )

        return HookResult(decision=HookDecision.ALLOW)


# ---------------------------------------------------------------------------
# 6. Slack Notifier
# ---------------------------------------------------------------------------

class SlackNotifier:
    """
    Sends notifications to Slack channels for critical events.
    Used for human escalation and observability.
    """

    # Channel routing
    CHANNEL_MAP = {
        "security": "#security-alerts",
        "escalation": "#escalations",
        "incident": "#incidents",
        "approval": "#approvals",
        "general": "#agent-activity",
    }

    def __init__(self, slack_client=None):
        self._client = slack_client
        self._pending_notifications: list[dict] = []

    def notify(
        self,
        category: str,
        title: str,
        details: str,
        context: AgentContext | None = None,
        priority: str = "medium",
    ) -> dict:
        channel = self.CHANNEL_MAP.get(category, self.CHANNEL_MAP["general"])

        notification = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "category": category,
            "priority": priority,
            "title": title,
            "details": details,
            "agent_id": context.agent_id if context else "system",
            "department": context.department if context else "system",
        }

        self._pending_notifications.append(notification)

        if self._client:
            try:
                self._client.send_message(
                    channel=channel,
                    text=f"*[{priority.upper()}] {title}*\n{details}\n_Agent: {notification['agent_id']}_",
                )
            except Exception as e:
                logger.error("Failed to send Slack notification: %s", e)

        logger.info("NOTIFY | %s | %s | %s", channel, priority, title)
        return notification

    def get_pending(self) -> list[dict]:
        return list(self._pending_notifications)


# ---------------------------------------------------------------------------
# Hook Chain Orchestrator
# ---------------------------------------------------------------------------

class HookChain:
    """
    Composes all hooks into a single pre/post tool-use pipeline.
    This is the main entry point for the governance layer.
    """

    def __init__(
        self,
        audit_logger: AuditLogger | None = None,
        rate_limiter: RateLimiter | None = None,
        auth_checker: AuthChecker | None = None,
        cost_tracker: CostTracker | None = None,
        compliance_checker: ComplianceChecker | None = None,
        slack_notifier: SlackNotifier | None = None,
        hitl_gateway=None,
        dashboard_url: str = "http://localhost:5000",
    ):
        self.audit = audit_logger or AuditLogger()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.auth = auth_checker or AuthChecker()
        self.cost = cost_tracker or CostTracker()
        self.compliance = compliance_checker or ComplianceChecker()
        self.slack = slack_notifier or SlackNotifier()
        self._hitl = hitl_gateway
        self._dashboard_url = dashboard_url

    def _create_approval_from_hook(
        self,
        context: AgentContext,
        tool_name: str,
        reason: str,
    ) -> HookResult:
        """Bridge ASK_HUMAN decisions to the HITL gateway.

        Creates an approval request, sends a Slack notification with a
        dashboard link, and returns a HookResult with the request ID.
        Falls back to BLOCK if no HITL gateway is configured.
        """
        if not self._hitl:
            logger.warning("ASK_HUMAN with no HITL gateway — falling back to BLOCK")
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"{reason} (no HITL gateway configured — blocked)",
            )

        req_id = self._hitl.request_approval(
            requesting_agent=context.agent_id,
            department=context.department,
            category="tool_authorization",
            title=f"Tool approval: {tool_name}",
            description=reason,
            risk_assessment="high",
            context={"tool_name": tool_name, "agent_tier": context.tier},
        )

        dashboard_link = f"{self._dashboard_url}/api/approvals/{req_id}"
        self.slack.notify(
            "approval",
            f"Approval needed: {tool_name}",
            f"{reason}\nAgent: {context.agent_id} ({context.department})\n"
            f"Review: {dashboard_link}",
            context,
            priority="high",
        )

        return HookResult(
            decision=HookDecision.ASK_HUMAN,
            reason=reason,
            metadata={"approval_request_id": req_id},
        )

    def pre_tool_use(
        self,
        context: AgentContext,
        tool_name: str,
        tool_input: dict | None = None,
    ) -> HookResult:
        """
        Run all pre-tool-use checks. Returns BLOCK on first failure.
        Order matters: cheapest checks first.
        """
        # 0. Budget pre-check (prevent overspend before API call)
        budget_result = self.cost.pre_check(context)
        if budget_result.decision != HookDecision.ALLOW:
            self.audit.log(context, "pre_tool_use", tool_name, tool_input,
                          decision="blocked", reasoning=budget_result.reason)
            self.slack.notify("escalation", "Agent budget nearly exhausted",
                           budget_result.reason, context, "high")
            return budget_result

        # 1. Rate limit (cheapest check)
        result = self.rate_limiter.check(context)
        if result.decision != HookDecision.ALLOW:
            self.audit.log(context, "pre_tool_use", tool_name, tool_input,
                          decision="blocked", reasoning=result.reason)
            self.slack.notify("security", "Rate limit exceeded",
                           result.reason, context, "high")
            return result

        # 2. Auth check
        result = self.auth.check(context, tool_name, tool_input)
        if result.decision != HookDecision.ALLOW:
            self.audit.log(context, "pre_tool_use", tool_name, tool_input,
                          decision=result.decision.value, reasoning=result.reason)
            if result.decision == HookDecision.BLOCK:
                self.slack.notify("security", "Unauthorized tool access attempt",
                               result.reason, context, "high")
            elif result.decision == HookDecision.ASK_HUMAN:
                return self._create_approval_from_hook(context, tool_name, result.reason)
            return result

        # 3. Compliance check for external-facing tools
        if tool_input and tool_name in (
            "send_gmail_message", "send_message",
            "mcp__google-workspace__send_gmail_message",
            "mcp__google-workspace__send_message",
        ):
            body = tool_input.get("body", tool_input.get("text", ""))
            to = tool_input.get("to", "")
            subject = tool_input.get("subject", "")
            result = self.compliance.check_email(to, subject, body)
            if result.decision != HookDecision.ALLOW:
                self.audit.log(context, "pre_tool_use", tool_name, tool_input,
                              decision=result.decision.value, reasoning=result.reason)
                if result.decision == HookDecision.ASK_HUMAN:
                    return self._create_approval_from_hook(context, tool_name, result.reason)
                return result

        # All checks passed
        self.audit.log(context, "pre_tool_use", tool_name, tool_input,
                      decision="allowed")
        return HookResult(decision=HookDecision.ALLOW)

    def post_tool_use(
        self,
        context: AgentContext,
        tool_name: str,
        tool_input: dict | None = None,
        tool_output: dict | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> HookResult:
        """
        Run all post-tool-use hooks. Primarily for tracking and auditing.
        """
        # Track cost
        cost_result = self.cost.track(context, input_tokens, output_tokens)
        if cost_result.decision != HookDecision.ALLOW:
            self.audit.log(context, "post_tool_use", tool_name, tool_input,
                          tool_output, decision="budget_exceeded",
                          reasoning=cost_result.reason)
            self.slack.notify("escalation", "Agent budget exceeded",
                           cost_result.reason, context, "critical")
            return cost_result

        # Audit log
        self.audit.log(context, "post_tool_use", tool_name, tool_input,
                      tool_output, decision="completed",
                      reasoning=json.dumps(cost_result.metadata, default=str))

        return HookResult(decision=HookDecision.ALLOW, metadata=cost_result.metadata)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_hook_chain(
    db_writer=None,
    slack_client=None,
    config: dict | None = None,
    hitl_gateway=None,
    redis_url: str = "",
) -> HookChain:
    """Create a fully configured HookChain from company config."""
    cfg = config or {}

    rate_limits = cfg.get("rate_limits", {})
    budgets = cfg.get("budgets", {})

    max_session = rate_limits.get("max_tool_calls_per_session", 100)
    max_minute = rate_limits.get("max_api_calls_per_minute", 30)

    # Use Redis rate limiter if URL provided, otherwise in-memory
    if redis_url:
        from src.core.redis_rate_limiter import RedisRateLimiter
        rate_limiter = RedisRateLimiter(
            redis_url=redis_url,
            max_calls_per_session=max_session,
            max_calls_per_minute=max_minute,
        )
        # Fall back to in-memory if Redis connection failed
        if not rate_limiter.is_distributed:
            rate_limiter = RateLimiter(max_session, max_minute)
    else:
        rate_limiter = RateLimiter(max_session, max_minute)

    return HookChain(
        audit_logger=AuditLogger(db_writer=db_writer),
        rate_limiter=rate_limiter,
        auth_checker=AuthChecker(),
        cost_tracker=CostTracker(
            per_session_limit_usd=budgets.get("per_session_cost_limit_usd", 50.0),
        ),
        compliance_checker=ComplianceChecker(),
        slack_notifier=SlackNotifier(slack_client=slack_client),
        hitl_gateway=hitl_gateway,
    )
