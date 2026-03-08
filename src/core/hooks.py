"""
Governance hook chain for the AI company agent system.

Six hooks form the defense-in-depth governance layer:
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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

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
    Enforces per-session and per-minute rate limits on tool calls.
    Prevents runaway agent loops.
    """

    def __init__(
        self,
        max_calls_per_session: int = 100,
        max_calls_per_minute: int = 30,
    ):
        self.max_per_session = max_calls_per_session
        self.max_per_minute = max_calls_per_minute
        self._session_counts: dict[str, int] = {}
        self._minute_windows: dict[str, list[float]] = {}

    def check(self, context: AgentContext) -> HookResult:
        sid = context.session_id

        # Session-level check
        self._session_counts.setdefault(sid, 0)
        self._session_counts[sid] += 1
        if self._session_counts[sid] > self.max_per_session:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session {sid} exceeded {self.max_per_session} tool calls",
                metadata={"count": self._session_counts[sid]},
            )

        # Per-minute check
        now = time.time()
        self._minute_windows.setdefault(sid, [])
        window = self._minute_windows[sid]
        window.append(now)
        # Trim to last 60 seconds
        self._minute_windows[sid] = [t for t in window if now - t < 60]
        if len(self._minute_windows[sid]) > self.max_per_minute:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session {sid} exceeded {self.max_per_minute} calls/minute",
                metadata={"calls_in_window": len(self._minute_windows[sid])},
            )

        return HookResult(decision=HookDecision.ALLOW)

    def reset_session(self, session_id: str):
        self._session_counts.pop(session_id, None)
        self._minute_windows.pop(session_id, None)


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
    """

    # Pricing per million tokens (March 2026)
    PRICING = {
        "claude-opus-4-6": {"input": 5.0, "output": 25.0},
        "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    }

    def __init__(self, per_session_limit_usd: float = 50.0):
        self.per_session_limit = per_session_limit_usd
        self._session_costs: dict[str, float] = {}
        self._session_tokens: dict[str, dict[str, int]] = {}
        self._global_daily_tokens: int = 0
        self._daily_reset_date: str = ""

    def track(
        self,
        context: AgentContext,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> HookResult:
        sid = context.session_id
        model = context.model

        # Calculate cost
        pricing = self.PRICING.get(model, self.PRICING["claude-sonnet-4-5-20250514"])
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

        # Track session cost
        self._session_costs.setdefault(sid, 0.0)
        self._session_costs[sid] += cost

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

        # Check session limit
        if self._session_costs[sid] > self.per_session_limit:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session cost ${self._session_costs[sid]:.2f} exceeds limit ${self.per_session_limit:.2f}",
                metadata={
                    "session_cost": self._session_costs[sid],
                    "limit": self.per_session_limit,
                },
            )

        return HookResult(
            decision=HookDecision.ALLOW,
            metadata={
                "session_cost": self._session_costs[sid],
                "session_tokens": self._session_tokens[sid],
                "global_daily_tokens": self._global_daily_tokens,
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
    ):
        self.audit = audit_logger or AuditLogger()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.auth = auth_checker or AuthChecker()
        self.cost = cost_tracker or CostTracker()
        self.compliance = compliance_checker or ComplianceChecker()
        self.slack = slack_notifier or SlackNotifier()

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
) -> HookChain:
    """Create a fully configured HookChain from company config."""
    cfg = config or {}

    rate_limits = cfg.get("rate_limits", {})
    budgets = cfg.get("budgets", {})

    return HookChain(
        audit_logger=AuditLogger(db_writer=db_writer),
        rate_limiter=RateLimiter(
            max_calls_per_session=rate_limits.get("max_tool_calls_per_session", 100),
            max_calls_per_minute=rate_limits.get("max_api_calls_per_minute", 30),
        ),
        auth_checker=AuthChecker(),
        cost_tracker=CostTracker(
            per_session_limit_usd=budgets.get("per_session_cost_limit_usd", 50.0),
        ),
        compliance_checker=ComplianceChecker(),
        slack_notifier=SlackNotifier(slack_client=slack_client),
    )
