"""`notify__email` — send an email via the Gmail API.

Auditor agents (sre-gcp-auditor, drive-security-auditor, codebase-guardian)
end their runs by emailing a markdown/plain report to an operator. This tool
authenticates with the `FORGEOS_GWS_*` OAuth credentials (the same client used
elsewhere for Google Workspace) and sends as the authorized account.

Credential resolution (first hit wins):
  1. Environment: FORGEOS_GWS_CLIENT_ID / FORGEOS_GWS_CLIENT_SECRET /
     FORGEOS_GWS_REFRESH_TOKEN.
  2. Secret Manager: secrets of the same names in the project named by
     GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT (the Cloud Run service account has
     read access).

The access token is minted per-send from the refresh token (no long-lived
state). The default recipient is FORGEOS_AUDIT_EMAIL_TO if the caller omits
`to`. Errors are returned in the result dict (never raised) so the agent can
see the failure and report it.
"""
from __future__ import annotations

import base64
import logging
import os
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_GWS_SECRETS = {
    "client_id": "FORGEOS_GWS_CLIENT_ID",
    "client_secret": "FORGEOS_GWS_CLIENT_SECRET",
    "refresh_token": "FORGEOS_GWS_REFRESH_TOKEN",
}


def _project() -> str:
    return (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        or ""
    )


def _resolve_creds() -> dict[str, str] | str:
    """Return {client_id, client_secret, refresh_token} or an error string."""
    creds: dict[str, str] = {}
    missing_env = []
    for key, env_name in _GWS_SECRETS.items():
        val = os.environ.get(env_name, "").strip()
        if val:
            creds[key] = val
        else:
            missing_env.append((key, env_name))

    if not missing_env:
        return creds

    # Fall back to Secret Manager for whatever wasn't in env.
    project = _project()
    if not project:
        return (
            "missing Gmail credentials: set FORGEOS_GWS_CLIENT_ID/"
            "CLIENT_SECRET/REFRESH_TOKEN env or GCP_PROJECT_ID for Secret Manager"
        )
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        for key, env_name in missing_env:
            path = f"projects/{project}/secrets/{env_name}/versions/latest"
            resp = client.access_secret_version(name=path)
            creds[key] = resp.payload.data.decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001 — surface to the agent, don't crash
        return f"could not read Gmail credentials from Secret Manager: {e}"
    return creds


def _access_token(creds: dict[str, str]) -> str | dict[str, Any]:
    import requests

    resp = requests.post(
        _TOKEN_URI,
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        return {
            "ok": False,
            "error": f"token refresh failed ({resp.status_code}): {resp.text[:300]}. "
            "If invalid_grant, re-mint FORGEOS_GWS_REFRESH_TOKEN "
            "(scripts/mint_gws_token.py).",
        }
    return resp.json().get("access_token", "")


def send_email(
    *,
    subject: str,
    body: str,
    to: str | None = None,
    html: bool = False,
) -> dict[str, Any]:
    to = to or os.environ.get("FORGEOS_AUDIT_EMAIL_TO", "").strip()
    if not to:
        return {"ok": False, "error": "no recipient: pass `to` or set FORGEOS_AUDIT_EMAIL_TO"}

    creds = _resolve_creds()
    if isinstance(creds, str):
        return {"ok": False, "error": creds}

    token = _access_token(creds)
    if isinstance(token, dict):
        return token
    if not token:
        return {"ok": False, "error": "empty access token from refresh"}

    msg = MIMEText(body, "html" if html else "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    import requests

    resp = requests.post(
        _GMAIL_SEND,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"raw": raw},
        timeout=30,
    )
    if resp.status_code not in (200, 202):
        return {"ok": False, "error": f"gmail send failed ({resp.status_code}): {resp.text[:300]}"}
    data = resp.json()
    return {"ok": True, "to": to, "subject": subject, "message_id": data.get("id", "")}


EMAIL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "notify__email",
        "description": (
            "Send an email report via Gmail. Use this at the end of an audit "
            "run to deliver findings to the operator. `body` is plain text by "
            "default (set html=true for an HTML body). If `to` is omitted, the "
            "configured default recipient (FORGEOS_AUDIT_EMAIL_TO) is used. "
            "Returns {ok, message_id} or {ok:false, error}."
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
