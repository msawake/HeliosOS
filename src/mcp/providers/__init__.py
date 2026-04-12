"""
Real platform tool providers.

Each provider replaces the corresponding simulated handler in
`src/mcp/platform_tools.py` when enabled via env flag. The `resolve`
function returns either the real implementation or None (signalling
"fall back to simulated").

Usage from platform_tools._HANDLER_MAP:
    real = providers.resolve("platform__http_fetch")
    _HANDLER_MAP["platform__http_fetch"] = real or handle_http_fetch_simulated
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _env_true(key: str) -> bool:
    return os.environ.get(key, "").lower() in ("1", "true", "yes", "on")


# Lazy imports: only load providers when the env flag is set, so the
# platform starts fast and doesn't require optional deps.

_PROVIDER_LOADERS: dict[str, tuple[str, Callable[[], Any]]] = {}


def register_provider(tool_name: str, env_flag: str, loader: Callable[[], Any]) -> None:
    """Register a provider loader.

    - tool_name:  the fully-qualified tool name (e.g. 'platform__http_fetch')
    - env_flag:   env var that must be truthy for the loader to fire
    - loader:     zero-arg callable returning the handler function, or None
                  if dependencies are missing
    """
    _PROVIDER_LOADERS[tool_name] = (env_flag, loader)


def resolve(tool_name: str) -> Callable | None:
    """Return the real handler for a tool, or None (use simulated)."""
    entry = _PROVIDER_LOADERS.get(tool_name)
    if not entry:
        return None
    env_flag, loader = entry
    if not _env_true(env_flag):
        return None
    try:
        handler = loader()
        return handler
    except Exception as e:
        logger.warning("Provider loader for %s failed: %s", tool_name, e)
        return None


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------

def _load_http_fetch():
    from .http_provider import handle_http_fetch
    return handle_http_fetch

def _load_http_post():
    from .http_provider import handle_http_post
    return handle_http_post


def _load_send_message():
    from .messaging_provider import handle_send_message
    return handle_send_message

def _load_read_messages():
    from .messaging_provider import handle_read_messages
    return handle_read_messages


def _load_github_get_pr():
    from .github_provider import handle_github_get_pr
    return handle_github_get_pr

def _load_github_create_review():
    from .github_provider import handle_github_create_review
    return handle_github_create_review


def _load_crm_search_leads():
    from .crm_provider import handle_crm_search_leads
    return handle_crm_search_leads

def _load_crm_update_lead():
    from .crm_provider import handle_crm_update_lead
    return handle_crm_update_lead

def _load_crm_create_activity():
    from .crm_provider import handle_crm_create_activity
    return handle_crm_create_activity


# HTTP providers are gated on FORGEOS_ENABLE_REAL_HTTP=1
register_provider("platform__http_fetch", "FORGEOS_ENABLE_REAL_HTTP", _load_http_fetch)
register_provider("platform__http_post", "FORGEOS_ENABLE_REAL_HTTP", _load_http_post)

# Messaging providers — use FORGEOS_ENABLE_REAL_MESSAGING=1; these can run
# purely in-process via the existing PostgresAgentMessageStore or in-memory.
register_provider("platform__send_message", "FORGEOS_ENABLE_REAL_MESSAGING", _load_send_message)
register_provider("platform__read_messages", "FORGEOS_ENABLE_REAL_MESSAGING", _load_read_messages)

# GitHub providers require GITHUB_TOKEN; env flag is FORGEOS_ENABLE_REAL_GITHUB
register_provider("platform__github_get_pr", "FORGEOS_ENABLE_REAL_GITHUB", _load_github_get_pr)
register_provider("platform__github_create_review", "FORGEOS_ENABLE_REAL_GITHUB", _load_github_create_review)

# CRM-via-ontology providers — FORGEOS_ENABLE_REAL_CRM=1
register_provider("platform__crm_search_leads", "FORGEOS_ENABLE_REAL_CRM", _load_crm_search_leads)
register_provider("platform__crm_update_lead", "FORGEOS_ENABLE_REAL_CRM", _load_crm_update_lead)
register_provider("platform__crm_create_activity", "FORGEOS_ENABLE_REAL_CRM", _load_crm_create_activity)


def status() -> dict:
    """Return a dict of {tool_name: 'real' | 'simulated'} for diagnostics."""
    out = {}
    for name, (flag, _) in _PROVIDER_LOADERS.items():
        out[name] = "real" if _env_true(flag) else "simulated"
    return out
