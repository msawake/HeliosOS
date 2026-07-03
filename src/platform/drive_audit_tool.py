"""`drive__audit_sharing` — list Google Drive files with risky sharing.

drive-security-auditor uses this to enumerate files shared publicly or
domain-wide, with their permissions, so the agent can classify risk and email
a report. It authenticates with the same `FORGEOS_GWS_*` OAuth creds as
`notify__email` (the refresh token must include the
`drive.metadata.readonly` scope — see scripts/mint_gws_token.py).

Metadata only: this never reads file *contents*. Read-only.
"""
from __future__ import annotations

import logging
from typing import Any

from src.platform.gws_auth import _access_token, _resolve_creds

logger = logging.getLogger(__name__)

_DRIVE_LIST = "https://www.googleapis.com/drive/v3/files"

# Drive query terms for "shared beyond a specific named person":
#   anyoneWithLink / anyoneCanFind → public; domainWithLink / domainCanFind → whole org.
_DEFAULT_QUERY = (
    "visibility='anyoneWithLink' or visibility='anyoneCanFind' "
    "or visibility='domainWithLink' or visibility='domainCanFind'"
)

_FIELDS = (
    "nextPageToken,files(id,name,mimeType,owners(emailAddress),"
    "webViewLink,shared,permissions(type,role,emailAddress,domain,allowFileDiscovery))"
)


def audit_sharing(
    *, query: str | None = None, max_files: int = 200
) -> dict[str, Any]:
    creds = _resolve_creds()
    if isinstance(creds, str):
        return {"ok": False, "error": creds}
    token = _access_token(creds)
    if isinstance(token, dict):
        return token
    if not token:
        return {"ok": False, "error": "empty access token from refresh"}

    import requests

    q = query or _DEFAULT_QUERY
    files: list[dict] = []
    page_token = ""
    try:
        while len(files) < max_files:
            params = {
                "q": q,
                "fields": _FIELDS,
                "pageSize": min(100, max_files - len(files)),
                "corpora": "allDrives",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = requests.get(
                _DRIVE_LIST,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": f"drive list failed ({resp.status_code}): {resp.text[:300]}. "
                    "If 403/insufficient scope, re-mint the token with "
                    "drive.metadata.readonly (scripts/mint_gws_token.py).",
                    "files_so_far": files,
                }
            data = resp.json()
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive audit error: {e}", "files_so_far": files}

    return {"ok": True, "query": q, "count": len(files), "files": files}


DRIVE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "drive__audit_sharing",
        "description": (
            "List Google Drive files with risky sharing (public link, public "
            "discoverable, or whole-domain) and their permissions. Read-only, "
            "metadata only — does not read file contents. Returns "
            "{ok, count, files:[{id,name,owners,webViewLink,permissions}]}. "
            "Pass a custom Drive `query` to narrow (default lists all "
            "public/domain-shared files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Drive v3 query (q). Default: all public/domain-shared files."},
                "max_files": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000},
            },
        },
    },
]


async def _handle_drive_audit(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio

    result = await asyncio.to_thread(audit_sharing, **tool_input)
    return {"success": result.get("ok", False), "result": result}


DRIVE_TOOL_HANDLERS: dict[str, Any] = {
    "drive__audit_sharing": _handle_drive_audit,
}
