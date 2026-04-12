"""
Real inter-agent messaging provider.

Replaces the in-memory `_agent_mailboxes` dict in `platform_tools.py` with
a proper async-safe store. When the platform's EventBus is available via
`agent_context["_event_bus"]`, delegates to `EventBus.send_message` (which
may be backed by `PostgresAgentMessageStore` when a DB is connected).

Otherwise falls back to a module-level in-memory dict (same behavior as
simulated, but cleanly isolated here).

Env flag: FORGEOS_ENABLE_REAL_MESSAGING=1
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Fallback in-memory store (when EventBus is unavailable).
# Keyed by recipient agent_id.
_fallback_mailbox: dict[str, list[dict]] = defaultdict(list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_event_bus(agent_context: dict | None):
    """Try to obtain an EventBus from several possible context paths."""
    if not agent_context:
        return None
    bus = agent_context.get("_event_bus")
    if bus is not None:
        return bus
    # Sometimes the company_system is threaded through
    system = agent_context.get("_company_system") or agent_context.get("company_system")
    if system is not None and hasattr(system, "event_bus"):
        return system.event_bus
    return None


def _run_sync(coro):
    """Run an async coro from a sync context. Used because tool handlers
    are currently sync callables."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside a running loop — schedule + wait in a separate thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def handle_send_message(tool_input: dict, agent_context: dict | None) -> dict:
    """Real inter-agent message send."""
    recipient = tool_input.get("recipient") or tool_input.get("to") or ""
    if not recipient:
        return {"success": False, "error": "recipient is required"}

    sender = (agent_context or {}).get("agent_id", "unknown")
    subject = tool_input.get("subject", "")
    body = tool_input.get("body", "")
    priority = tool_input.get("priority", "normal")
    metadata = tool_input.get("metadata", {})

    content = {
        "subject": subject,
        "body": body,
        "priority": priority,
        "metadata": metadata,
        "sent_at": _now_iso(),
    }

    bus = _get_event_bus(agent_context)
    if bus is not None and hasattr(bus, "send_message"):
        try:
            msg_id = _run_sync(bus.send_message(sender, recipient, content))
            return {
                "success": True,
                "message_id": str(msg_id),
                "delivered_to": recipient,
                "sent_at": content["sent_at"],
                "backend": "event_bus",
            }
        except Exception as e:
            logger.warning("EventBus send_message failed, using fallback: %s", e)

    # In-memory fallback
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    message = {
        "message_id": msg_id,
        "from": sender,
        "to": recipient,
        **content,
        "read": False,
    }
    _fallback_mailbox[recipient].append(message)
    return {
        "success": True,
        "message_id": msg_id,
        "delivered_to": recipient,
        "sent_at": content["sent_at"],
        "backend": "in_memory",
    }


def handle_read_messages(tool_input: dict, agent_context: dict | None) -> dict:
    """Read messages from the calling agent's mailbox."""
    agent_id = (agent_context or {}).get("agent_id", tool_input.get("agent_id", "unknown"))
    unread_only = bool(tool_input.get("unread_only", False))
    limit = int(tool_input.get("limit", 20))

    bus = _get_event_bus(agent_context)
    if bus is not None and hasattr(bus, "get_messages"):
        try:
            messages = _run_sync(bus.get_messages(agent_id, unread_only=unread_only))
            # Trim to limit
            messages = list(messages)[:limit]
            return {
                "success": True,
                "agent_id": agent_id,
                "count": len(messages),
                "messages": messages,
                "backend": "event_bus",
            }
        except Exception as e:
            logger.warning("EventBus get_messages failed, using fallback: %s", e)

    # In-memory fallback
    mailbox = _fallback_mailbox.get(agent_id, [])
    if unread_only:
        mailbox = [m for m in mailbox if not m.get("read")]
    result = mailbox[:limit]
    return {
        "success": True,
        "agent_id": agent_id,
        "count": len(result),
        "messages": result,
        "backend": "in_memory",
    }
