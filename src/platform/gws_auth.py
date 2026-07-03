"""Google Workspace OAuth helpers — mint an access token from the
`FORGEOS_GWS_*` refresh-token credentials.

Shared by tools that call Google APIs as the authorized Workspace account
(currently `drive__audit_sharing` in `drive_audit_tool.py`). Kept separate from
`email_tool.py` (which now sends via Resend) so email no longer depends on GWS.

Credential resolution (first hit wins):
  1. Environment: FORGEOS_GWS_CLIENT_ID / FORGEOS_GWS_CLIENT_SECRET /
     FORGEOS_GWS_REFRESH_TOKEN.
  2. Secret Manager: secrets of the same names in the project named by
     GCP_PROJECT_ID / GOOGLE_CLOUD_PROJECT.
"""
from __future__ import annotations

import os
from typing import Any

_TOKEN_URI = "https://oauth2.googleapis.com/token"

_GWS_SECRETS = {
    "client_id": "FORGEOS_GWS_CLIENT_ID",
    "client_secret": "FORGEOS_GWS_CLIENT_SECRET",
    "refresh_token": "FORGEOS_GWS_REFRESH_TOKEN",
}


def _project() -> str:
    env = (
        os.environ.get("GCP_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        or ""
    ).strip()
    if env:
        return env
    try:
        from google.auth import default as google_default
        _creds, project = google_default()
        return (project or "").strip()
    except Exception:  # noqa: BLE001
        return ""


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

    project = _project()
    if not project:
        return (
            "missing GWS credentials: set FORGEOS_GWS_CLIENT_ID/"
            "CLIENT_SECRET/REFRESH_TOKEN env or GCP_PROJECT_ID for Secret Manager"
        )
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        for key, env_name in missing_env:
            path = f"projects/{project}/secrets/{env_name}/versions/latest"
            resp = client.access_secret_version(name=path)
            creds[key] = resp.payload.data.decode("utf-8").strip()
    except Exception as e:  # noqa: BLE001 — surface to the caller, don't crash
        return f"could not read GWS credentials from Secret Manager: {e}"
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
