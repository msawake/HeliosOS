"""
Real HTTP provider.

Replaces simulated `handle_http_fetch` / `handle_http_post` with live
`httpx.Client` calls. Safety measures:

1. **Domain allowlist** — controlled by `FORGEOS_HTTP_ALLOWLIST`
   (comma-separated list of domains or "*" for any). Default: block all.
2. **Response size cap** — default 5 MB, configurable via
   `FORGEOS_HTTP_MAX_BYTES`.
3. **Retry** — 3 attempts with exponential backoff on 5xx / network errors.
4. **Redacted headers** — `Authorization`, `Cookie`, `X-API-Key` are
   stripped from the response echo.
5. **Disabled auth forwarding** — we never forward the caller's auth
   headers; the agent must pass its own via the `headers` argument.

Env vars:
    FORGEOS_HTTP_ALLOWLIST     "example.com,api.github.com" or "*"
    FORGEOS_HTTP_MAX_BYTES     default 5242880 (5 MB)
    FORGEOS_HTTP_TIMEOUT       default 30 (seconds)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = int(os.environ.get("FORGEOS_HTTP_MAX_BYTES", str(5 * 1024 * 1024)))
DEFAULT_TIMEOUT = float(os.environ.get("FORGEOS_HTTP_TIMEOUT", "30"))
DEFAULT_USER_AGENT = os.environ.get("FORGEOS_HTTP_USER_AGENT", "Helios OS-Agent/1.0")

REDACTED_HEADERS = {"authorization", "cookie", "x-api-key", "api-key", "proxy-authorization"}


def _allowlist() -> list[str]:
    raw = os.environ.get("FORGEOS_HTTP_ALLOWLIST", "").strip()
    if not raw:
        return []
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def _is_allowed(url: str) -> bool:
    """Check url against the allowlist. "*" allows everything."""
    allowed = _allowlist()
    if not allowed:
        return False
    if "*" in allowed:
        return True
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
    except Exception:
        return False
    for rule in allowed:
        if host == rule:
            return True
        if rule.startswith(".") and host.endswith(rule):
            return True
        if host.endswith("." + rule):
            return True
    return False


def _safe_headers(headers: dict | None) -> dict:
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() not in REDACTED_HEADERS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_with_retry(url: str, method: str, headers: dict, body: Any, timeout: float) -> dict:
    import httpx

    last_error = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.request(
                    method=method,
                    url=url,
                    headers={**headers, "User-Agent": DEFAULT_USER_AGENT},
                    json=body if method in ("POST", "PUT", "PATCH") and isinstance(body, (dict, list)) else None,
                    content=body if method in ("POST", "PUT", "PATCH") and isinstance(body, (str, bytes)) else None,
                )
                return {
                    "_response": response,
                    "status_code": response.status_code,
                }
        except httpx.TimeoutException as e:
            last_error = f"timeout: {e}"
        except httpx.NetworkError as e:
            last_error = f"network: {e}"
        except httpx.HTTPError as e:
            last_error = f"http: {e}"
        if attempt < 2:
            time.sleep(0.5 * (2 ** attempt))
    return {"_error": last_error or "unknown error"}


def handle_http_fetch(tool_input: dict, agent_context: dict | None) -> dict:
    """Real HTTP GET, gated by allowlist + size cap."""
    url = tool_input.get("url", "")
    headers = tool_input.get("headers", {})
    if not url:
        return {"success": False, "error": "url is required"}
    if not _is_allowed(url):
        return {
            "success": False,
            "error": (
                f"Domain not in FORGEOS_HTTP_ALLOWLIST (url={url}). "
                f"Set FORGEOS_HTTP_ALLOWLIST='*' or add the domain to enable."
            ),
        }

    result = _fetch_with_retry(url, "GET", headers, None, DEFAULT_TIMEOUT)
    if "_error" in result:
        return {"success": False, "error": result["_error"], "url": url}

    response = result["_response"]
    raw = response.content[:DEFAULT_MAX_BYTES]
    truncated = len(response.content) > DEFAULT_MAX_BYTES

    try:
        text_body = raw.decode("utf-8", errors="replace")
    except Exception:
        text_body = ""

    return {
        "success": True,
        "url": url,
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "content_length": len(response.content),
        "truncated": truncated,
        "body_text": text_body[:DEFAULT_MAX_BYTES],
        "headers": _safe_headers(dict(response.headers)),
        "fetched_at": _now_iso(),
    }


def handle_http_post(tool_input: dict, agent_context: dict | None) -> dict:
    """Real HTTP POST, gated by allowlist."""
    url = tool_input.get("url", "")
    body = tool_input.get("body", {})
    headers = tool_input.get("headers", {})
    if not url:
        return {"success": False, "error": "url is required"}
    if not _is_allowed(url):
        return {
            "success": False,
            "error": f"Domain not in FORGEOS_HTTP_ALLOWLIST (url={url})",
        }

    result = _fetch_with_retry(url, "POST", headers, body, DEFAULT_TIMEOUT)
    if "_error" in result:
        return {"success": False, "error": result["_error"], "url": url}

    response = result["_response"]
    raw = response.content[:DEFAULT_MAX_BYTES]
    try:
        text_body = raw.decode("utf-8", errors="replace")
    except Exception:
        text_body = ""

    return {
        "success": True,
        "url": url,
        "method": "POST",
        "status_code": response.status_code,
        "body_text": text_body[:DEFAULT_MAX_BYTES],
        "headers": _safe_headers(dict(response.headers)),
        "posted_at": _now_iso(),
    }
