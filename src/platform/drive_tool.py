"""Service-account–backed Google Drive tools (`drive__list/read/update/create/find`).

Unlike `drive_audit_tool.py` (which rides the operator's personal OAuth refresh
token), this module authenticates as a **dedicated service account**, impersonated
from the platform's Cloud Run runtime SA. The keyless impersonation pattern is
the one recommended by the security proposal (`docs/security/agent-mcp-security-proposal.md`):

    Cloud Run runtime SA  ──[token-creator]──▶  forgeos-drive-agent SA  ──▶  Drive API

The runtime SA holds `roles/iam.serviceAccountTokenCreator` on the drive-agent
SA; no key files exist anywhere. Tokens are minted per-need with a short
lifetime and the narrow `drive.file` scope, which limits the SA to files the
user has explicitly shared with its email — that *is* the "user authorizes the
agent" mechanism, accomplished by sharing files with the SA in the Drive UI.

Configuration (env, with sensible defaults):
    FORGEOS_DRIVE_AGENT_SA   email of the dedicated SA (e.g.
                             forgeos-drive-agent@<project>.iam.gserviceaccount.com)
    FORGEOS_DRIVE_SCOPES     comma-separated; default "drive.file"

If the env is unset, the tools return a clear error explaining how to set it up
rather than silently falling back to the runtime SA's ADC (which would be the
wrong identity).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DRIVE_API = "https://www.googleapis.com/drive/v3/files"
_DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

# Drive scopes the SA needs. drive.file = SA can read/write ONLY files shared
# with its email (least privilege). Use "drive" for full access only if you
# really need it; we deliberately don't make that the default.
_DEFAULT_SCOPE = "https://www.googleapis.com/auth/drive.file"

# (sa_email, scope_csv) -> (access_token, expiry_unix_ts)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_TTL_S = 900  # 15 min — matches proposal §11.0 "short-lived" guidance

# Google-native target mime → default source content type Drive will convert.
# Override per-call with `source_mime_type` if needed.
_NATIVE_SOURCE_MIME = {
    "application/vnd.google-apps.document": "text/html",       # supports headings, bullets, bold
    "application/vnd.google-apps.spreadsheet": "text/csv",     # one row = one row; first row = header
    "application/vnd.google-apps.presentation": "text/plain",
}


def _resolve_upload_mime(target_mime: str, source_mime_type: str | None) -> str:
    """For Google-native target types Drive needs the body's *source* content
    type (text/html, text/csv, …), not the target type. Auto-map unless the
    caller passed an explicit source_mime_type."""
    if source_mime_type:
        return source_mime_type
    if target_mime in _NATIVE_SOURCE_MIME:
        return _NATIVE_SOURCE_MIME[target_mime]
    if target_mime and target_mime.startswith("application/vnd.google-apps."):
        return "text/plain"
    # Regular file (text/markdown, text/plain, etc.) — body type == target type.
    return target_mime or "text/plain"


def _target_sa() -> str:
    return os.environ.get("FORGEOS_DRIVE_AGENT_SA", "").strip()


def _scopes() -> list[str]:
    raw = os.environ.get("FORGEOS_DRIVE_SCOPES", "").strip()
    if not raw:
        return [_DEFAULT_SCOPE]
    out = []
    for piece in raw.split(","):
        s = piece.strip()
        if not s:
            continue
        # Allow "drive.file" or full URL forms.
        if s.startswith("http"):
            out.append(s)
        else:
            out.append(f"https://www.googleapis.com/auth/{s}")
    return out or [_DEFAULT_SCOPE]


def _get_impersonated_token() -> dict[str, Any] | str:
    """Return access token by impersonating `FORGEOS_DRIVE_AGENT_SA`. Cached
    until close to expiry. On error returns an error dict (callers detect via
    isinstance(_, dict))."""
    sa = _target_sa()
    if not sa:
        return {
            "ok": False,
            "error": (
                "FORGEOS_DRIVE_AGENT_SA is not set. Create a dedicated SA "
                "(e.g. forgeos-drive-agent@<project>.iam.gserviceaccount.com), "
                "grant the Cloud Run runtime SA roles/iam.serviceAccountTokenCreator "
                "on it, share your Drive folder with the SA's email, and set "
                "FORGEOS_DRIVE_AGENT_SA on the platform service."
            ),
        }
    scope_list = _scopes()
    cache_key = (sa, ",".join(sorted(scope_list)))
    now = time.time()
    cached = _token_cache.get(cache_key)
    if cached and cached[1] - now > 60:
        return cached[0]

    try:
        from google.auth import default as google_default, impersonated_credentials
        from google.auth.transport.requests import Request as _GoogleRequest
    except ImportError as e:
        return {"ok": False, "error": f"google-auth not installed: {e}"}

    try:
        source, _proj = google_default()
        target = impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=sa,
            target_scopes=scope_list,
            lifetime=_TOKEN_TTL_S,
        )
        target.refresh(_GoogleRequest())
        token = target.token
        if not token:
            return {"ok": False, "error": "impersonation returned empty token"}
        _token_cache[cache_key] = (token, now + _TOKEN_TTL_S)
        return token
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": (
                f"failed to impersonate {sa}: {e}. Check that the Cloud Run "
                f"runtime SA has roles/iam.serviceAccountTokenCreator on the "
                f"target SA, and that the IAM Credentials API is enabled."
            ),
        }


def _auth_headers() -> dict[str, str] | dict[str, Any]:
    tok = _get_impersonated_token()
    if isinstance(tok, dict):
        return tok  # error dict
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# Drive operations
# ---------------------------------------------------------------------------

def list_files(
    *,
    folder_id: str | None = None,
    query: str | None = None,
    max_files: int = 50,
) -> dict[str, Any]:
    """List files visible to the drive-agent SA (i.e. shared with its email).
    Optionally scoped to a folder_id and/or a Drive v3 `q` query."""
    headers = _auth_headers()
    if "Authorization" not in headers:
        return headers  # error
    import requests

    # Default: only files shared with the SA, not trashed.
    q_parts = ["trashed = false"]
    if folder_id:
        q_parts.append(f"'{folder_id}' in parents")
    if query:
        q_parts.append(f"({query})")
    q = " and ".join(q_parts)

    fields = "files(id,name,mimeType,modifiedTime,owners(emailAddress),parents,webViewLink,size),nextPageToken"
    files: list[dict] = []
    page_token = ""
    try:
        while len(files) < max_files:
            params = {
                "q": q,
                "fields": fields,
                "pageSize": min(100, max_files - len(files)),
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "corpora": "allDrives",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = requests.get(_DRIVE_API, headers=headers, params=params, timeout=20)
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": f"drive list failed ({resp.status_code}): {resp.text[:300]}",
                    "files_so_far": files,
                }
            data = resp.json()
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken") or ""
            if not page_token:
                break
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive list error: {e}", "files_so_far": files}

    return {"ok": True, "query": q, "count": len(files), "files": files}


def find_by_name(*, name: str, folder_id: str | None = None) -> dict[str, Any]:
    """Convenience: find a file by exact name (and optional folder)."""
    # Escape single quotes in Drive query.
    safe = name.replace("'", "\\'")
    q = f"name = '{safe}'"
    return list_files(folder_id=folder_id, query=q, max_files=10)


def read_file(*, file_id: str, max_bytes: int = 200_000) -> dict[str, Any]:
    """Return text content of a Drive file. For Google Docs the file is
    exported as text/plain. For native files (text/markdown, text/plain, …)
    the bytes are downloaded with alt=media. Binary files are refused.

    Returns {ok, file_id, name?, mime_type, content (str), truncated (bool)}."""
    headers = _auth_headers()
    if "Authorization" not in headers:
        return headers
    import requests

    # First fetch metadata to know the mime type / name.
    try:
        meta = requests.get(
            f"{_DRIVE_API}/{file_id}",
            headers=headers,
            params={
                "fields": "id,name,mimeType,size",
                "supportsAllDrives": "true",
            },
            timeout=15,
        )
        if meta.status_code != 200:
            return {
                "ok": False,
                "error": f"drive get metadata failed ({meta.status_code}): {meta.text[:300]}",
            }
        m = meta.json()
        mime = m.get("mimeType", "")
        name = m.get("name", "")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive get metadata error: {e}"}

    # Google-native types must be exported.
    export_map = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    try:
        if mime in export_map:
            r = requests.get(
                f"{_DRIVE_API}/{file_id}/export",
                headers=headers,
                params={"mimeType": export_map[mime]},
                timeout=30,
            )
        else:
            # Reject obvious binaries up front to avoid downloading them.
            if mime and not (
                mime.startswith("text/")
                or mime in {"application/json", "application/xml", "application/yaml"}
            ):
                return {
                    "ok": False,
                    "error": f"refusing to read non-text mime '{mime}'. "
                    "drive__read_file is for text/MD/JSON/etc. only.",
                    "name": name,
                    "mime_type": mime,
                }
            r = requests.get(
                f"{_DRIVE_API}/{file_id}",
                headers=headers,
                params={"alt": "media", "supportsAllDrives": "true"},
                timeout=30,
            )
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"drive read failed ({r.status_code}): {r.text[:300]}",
                "name": name,
                "mime_type": mime,
            }
        body = r.content or b""
        truncated = False
        if len(body) > max_bytes:
            body = body[:max_bytes]
            truncated = True
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "file_id": file_id,
            "name": name,
            "mime_type": mime,
            "content": text,
            "truncated": truncated,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive read error: {e}", "name": name, "mime_type": mime}


def update_file(
    *, file_id: str, content: str,
    mime_type: str | None = None, source_mime_type: str | None = None,
) -> dict[str, Any]:
    """Overwrite a Drive file's content with `content`. The file must already
    exist (use drive__create_file to make a new one). For Google Docs, this
    will overwrite the doc body via the upload endpoint — Drive will accept
    text/plain and update the underlying Doc. Returns {ok, file_id, name, mime_type}.
    """
    headers = _auth_headers()
    if "Authorization" not in headers:
        return headers
    import requests

    # Get the existing mime if not specified, so we don't accidentally rewrite
    # a markdown file as a doc.
    if not mime_type:
        try:
            meta = requests.get(
                f"{_DRIVE_API}/{file_id}",
                headers=headers,
                params={"fields": "name,mimeType", "supportsAllDrives": "true"},
                timeout=15,
            )
            if meta.status_code != 200:
                return {
                    "ok": False,
                    "error": f"drive metadata failed ({meta.status_code}): {meta.text[:300]}",
                }
            m = meta.json()
            mime_type = m.get("mimeType", "text/plain")
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"drive metadata error: {e}"}

    upload_mime = _resolve_upload_mime(mime_type or "", source_mime_type)

    try:
        resp = requests.patch(
            f"{_DRIVE_UPLOAD}/{file_id}",
            headers={
                **headers,
                "Content-Type": upload_mime or "text/plain",
            },
            params={"uploadType": "media", "supportsAllDrives": "true"},
            data=content.encode("utf-8"),
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return {
                "ok": False,
                "error": f"drive update failed ({resp.status_code}): {resp.text[:300]}",
            }
        body = resp.json()
        return {
            "ok": True,
            "file_id": body.get("id", file_id),
            "name": body.get("name"),
            "mime_type": body.get("mimeType", mime_type),
            "bytes_written": len(content.encode("utf-8")),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive update error: {e}"}


def create_file(
    *,
    name: str,
    content: str = "",
    folder_id: str | None = None,
    mime_type: str = "text/markdown",
    source_mime_type: str | None = None,
) -> dict[str, Any]:
    """Create a new Drive file. If `folder_id` is given, the file is created
    inside that folder (the SA must be Editor on the folder). Multipart upload
    so metadata + body land in one request. Returns {ok, file_id, name, webViewLink}.
    """
    headers = _auth_headers()
    if "Authorization" not in headers:
        return headers
    import requests

    metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
    if folder_id:
        metadata["parents"] = [folder_id]

    # For Google-native target types (Docs/Sheets/Slides) the multipart body's
    # Content-Type must be the SOURCE type Drive should convert from (text/html
    # for Docs, text/csv for Sheets), not the target type.
    upload_ct = _resolve_upload_mime(mime_type, source_mime_type)

    boundary = "===forgeos-drive-boundary==="
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {upload_ct}\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")

    try:
        resp = requests.post(
            _DRIVE_UPLOAD,
            headers={
                **headers,
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            params={
                "uploadType": "multipart",
                "supportsAllDrives": "true",
                "fields": "id,name,mimeType,webViewLink,parents",
            },
            data=body,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            return {
                "ok": False,
                "error": f"drive create failed ({resp.status_code}): {resp.text[:300]}",
            }
        out = resp.json()
        return {
            "ok": True,
            "file_id": out.get("id"),
            "name": out.get("name"),
            "mime_type": out.get("mimeType"),
            "web_view_link": out.get("webViewLink"),
            "parents": out.get("parents", []),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"drive create error: {e}"}


# ---------------------------------------------------------------------------
# Tool schemas + handlers
# ---------------------------------------------------------------------------

DRIVE_RW_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "drive__list_files",
        "description": (
            "List Google Drive files the agent has been granted access to (via "
            "service-account file-sharing). Optionally scoped to a folder_id "
            "or a Drive v3 `q` query. Returns {ok, count, files:[{id,name,mimeType,...}]}. "
            "The SA only sees files explicitly shared with its email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "Optional Drive folder id to scope the listing to."},
                "query": {"type": "string", "description": "Optional Drive v3 query fragment, e.g. \"mimeType='text/markdown'\"."},
                "max_files": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
            },
        },
    },
    {
        "name": "drive__find_by_name",
        "description": (
            "Find a Drive file by exact name, optionally within a folder_id. "
            "Returns the same shape as drive__list_files."
        ),
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "folder_id": {"type": "string"},
            },
        },
    },
    {
        "name": "drive__read_file",
        "description": (
            "Read the text content of a Drive file by id. Supports text/* files "
            "directly and Google Docs/Sheets/Slides via export. Binary files are "
            "refused. Returns {ok, file_id, name, mime_type, content, truncated}."
        ),
        "input_schema": {
            "type": "object",
            "required": ["file_id"],
            "properties": {
                "file_id": {"type": "string"},
                "max_bytes": {"type": "integer", "default": 200000, "minimum": 1, "maximum": 5000000},
            },
        },
    },
    {
        "name": "drive__update_file",
        "description": (
            "Overwrite an existing Drive file's content with `content`. The file "
            "must already exist (use drive__create_file otherwise). Preserves "
            "mime type unless you pass `mime_type`. For Google-native targets "
            "(Docs/Sheets/Slides) the body is sent as the right source type "
            "(text/html for Docs, text/csv for Sheets, text/plain for Slides). "
            "Override with `source_mime_type` if needed."
        ),
        "input_schema": {
            "type": "object",
            "required": ["file_id", "content"],
            "properties": {
                "file_id": {"type": "string"},
                "content": {"type": "string"},
                "mime_type": {"type": "string", "description": "Optional. Defaults to the existing file's mime type."},
                "source_mime_type": {"type": "string", "description": "Explicit Content-Type for the body. Auto-derived from mime_type when omitted."},
            },
        },
    },
    {
        "name": "drive__create_file",
        "description": (
            "Create a new Drive file with `content`. If `folder_id` is provided "
            "the file is placed in that folder (the SA must have Editor on it).\n"
            "For native Google formats set mime_type to:\n"
            "  • application/vnd.google-apps.document    — body is text/html (use <h1>, <ul>, <strong>, etc.)\n"
            "  • application/vnd.google-apps.spreadsheet — body is CSV (first row is header)\n"
            "  • application/vnd.google-apps.presentation — body is text/plain\n"
            "For regular text files use mime_type=text/markdown or text/plain. Returns {ok, file_id, name, web_view_link}."
        ),
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string", "default": ""},
                "folder_id": {"type": "string"},
                "mime_type": {"type": "string", "default": "text/markdown"},
                "source_mime_type": {"type": "string", "description": "Explicit Content-Type for the body. Auto-derived from mime_type when omitted."},
            },
        },
    },
]


async def _handle_list(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio
    result = await asyncio.to_thread(list_files, **tool_input)
    return {"success": result.get("ok", False), "result": result}


async def _handle_find(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio
    result = await asyncio.to_thread(find_by_name, **tool_input)
    return {"success": result.get("ok", False), "result": result}


async def _handle_read(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio
    result = await asyncio.to_thread(read_file, **tool_input)
    return {"success": result.get("ok", False), "result": result}


async def _handle_update(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio
    result = await asyncio.to_thread(update_file, **tool_input)
    return {"success": result.get("ok", False), "result": result}


async def _handle_create(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    import asyncio
    result = await asyncio.to_thread(create_file, **tool_input)
    return {"success": result.get("ok", False), "result": result}


DRIVE_RW_TOOL_HANDLERS: dict[str, Any] = {
    "drive__list_files": _handle_list,
    "drive__find_by_name": _handle_find,
    "drive__read_file": _handle_read,
    "drive__update_file": _handle_update,
    "drive__create_file": _handle_create,
}
