"""`notify__email` — send an email via the Resend API (https://resend.com).

Agents (auditors, treasury/law-firm reporters, …) end a run by emailing a
markdown/plain report to an operator. This tool sends through Resend.

Credential resolution (first hit wins), same pattern for each name:
  1. Environment variable.
  2. Secret Manager: a secret of the same name in the project named by
     GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT (the Cloud Run SA has read access).

Config:
  * RESEND_API_KEY      — Resend API key (``re_…``). Required.
  * RESEND_FROM         — verified sender, e.g. ``Helios OS <noreply@domain>``.
                          Required (Resend rejects unverified senders).
  * FORGEOS_AUDIT_EMAIL_TO — default recipient when the caller omits ``to``.

Errors are returned in the result dict (never raised) so the agent sees the
failure and can report it.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_RESEND_SEND = "https://api.resend.com/emails"


def _project() -> str:
    return (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        or ""
    )


def _resolve_secret(name: str) -> str:
    """Env first, then Secret Manager (secret named `name`). '' if unavailable."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    project = _project()
    if not project:
        return ""
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        path = f"projects/{project}/secrets/{name}/versions/latest"
        resp = client.access_secret_version(name=path)
        return resp.payload.data.decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001 — surface as "not found", don't crash
        logger.debug("secret %s not available from Secret Manager: %s", name, e)
        return ""


def send_email(
    *,
    subject: str,
    body: str,
    to: str | None = None,
    html: bool = False,
    from_: str | None = None,
) -> dict[str, Any]:
    to = to or os.environ.get("FORGEOS_AUDIT_EMAIL_TO", "").strip()
    if not to:
        return {"ok": False, "error": "no recipient: pass `to` or set FORGEOS_AUDIT_EMAIL_TO"}

    api_key = _resolve_secret("RESEND_API_KEY")
    if not api_key:
        return {"ok": False, "error": "RESEND_API_KEY not configured (env or Secret Manager)"}
    sender = (from_ or _resolve_secret("RESEND_FROM")).strip()
    if not sender:
        return {"ok": False, "error": "RESEND_FROM not configured (verified sender required)"}

    payload: dict[str, Any] = {
        "from": sender,
        "to": [to],
        "subject": subject,
        ("html" if html else "text"): body,
    }

    import requests

    resp = requests.post(
        _RESEND_SEND,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 201, 202):
        return {"ok": False, "error": f"resend send failed ({resp.status_code}): {resp.text[:300]}"}
    data = resp.json() if resp.content else {}
    return {"ok": True, "to": to, "subject": subject, "message_id": data.get("id", "")}


EMAIL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "notify__email",
        "description": (
            "Send an email via Resend. Use at the end of a run to deliver a "
            "report/notification to the operator. `body` is plain text by default "
            "(set html=true for an HTML body). If `to` is omitted, the configured "
            "default recipient (FORGEOS_AUDIT_EMAIL_TO) is used. The sender is the "
            "platform's verified Resend address. Returns {ok, message_id} or "
            "{ok:false, error}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email. Defaults to the operator address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Email body (plain text unless html=true)."},
                "html": {"type": "boolean", "default": False},
            },
            "required": ["subject", "body"],
        },
    },
]


async def _handle_email(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio

    result = await asyncio.to_thread(send_email, **tool_input)
    return {"success": result.get("ok", False), "result": result}


EMAIL_TOOL_HANDLERS: dict[str, Any] = {
    "notify__email": _handle_email,
}
