"""
Google Drive Read-Only Audit Tools — permission and sharing risk scanner.

Uses the Google Drive API via `google-api-python-client` with OAuth credentials.
All calls are read-only (drive.readonly scope). No write methods exist in this module.

Supports two modes:
  - **Single-user**: Uses OAuth refresh token from .env (jamatest's Drive)
  - **Org-wide**: Uses service account with domain-wide delegation to impersonate
    each user in the Google Workspace org

Defense in depth:
  1. Code: only `files().list()` and `permissions().list()` — no writes
  2. OAuth: `drive.readonly` scope — API rejects any write attempt
  3. Kernel: manifest denies share/remove/set permission tools
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("drive-auditor.tools")

COMPANY_DOMAIN = os.environ.get("COMPANY_DOMAIN", "example.com")
GOOGLE_USER_EMAIL = os.environ.get("GOOGLE_USER_EMAIL", "user@example.com")

SENSITIVE_PATTERNS = [
    "contract", "nda", "salary", "compensation", "payroll",
    "budget", "financial", "revenue", "p&l", "profit",
    "credential", "password", "api key", "secret", "token",
    "confidential", "internal only", "restricted",
    "board", "acquisition", "merger", "due diligence",
    "employee", "performance review", "termination",
    "client list", "customer", "pricing",
]

_drive_service = None
_admin_service = None


def _get_drive_service(impersonate_email: str | None = None):
    """Build a Google Drive API service client (read-only)."""
    global _drive_service

    sa_key_file = os.environ.get("GOOGLE_SA_KEY_FILE", "")

    if sa_key_file and impersonate_email:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            sa_key_file,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            subject=impersonate_email,
        )
        return build("drive", "v3", credentials=creds)

    if _drive_service is not None:
        return _drive_service

    # Try OAuth credentials from .env first
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        client_id = os.environ.get("GOOGLE_WORKSPACE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_WORKSPACE_CLIENT_SECRET", "")
        refresh_token = os.environ.get("GOOGLE_WORKSPACE_REFRESH_TOKEN", "")

        if all([client_id, client_secret, refresh_token]):
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            import google.auth.transport.requests
            creds.refresh(google.auth.transport.requests.Request())
            _drive_service = build("drive", "v3", credentials=creds)
            logger.info("Drive: using OAuth credentials")
            return _drive_service
    except Exception as e:
        logger.warning("OAuth credentials failed (%s) — trying ADC", e)

    # Fallback: Application Default Credentials (gcloud auth)
    try:
        import google.auth
        import google.auth.transport.requests
        from googleapiclient.discovery import build

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
        creds.refresh(google.auth.transport.requests.Request())
        _drive_service = build("drive", "v3", credentials=creds)
        logger.info("Drive: using Application Default Credentials")
        return _drive_service
    except Exception as e:
        logger.error("Failed to build Drive service: %s", e)
        return None


def _get_admin_service():
    """Build Admin Directory API client for listing org users."""
    global _admin_service
    if _admin_service is not None:
        return _admin_service

    sa_key_file = os.environ.get("GOOGLE_SA_KEY_FILE", "")
    admin_email = os.environ.get("GOOGLE_ADMIN_EMAIL", GOOGLE_USER_EMAIL)

    if not sa_key_file:
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            sa_key_file,
            scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
            subject=admin_email,
        )
        _admin_service = build("admin", "directory_v1", credentials=creds)
        return _admin_service
    except Exception as e:
        logger.warning("Admin API not available (single-user mode): %s", e)
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def list_org_users() -> dict[str, Any]:
    """List all users in the Google Workspace org (requires admin delegation)."""
    admin = _get_admin_service()
    if admin is None:
        return {
            "users": [{"email": GOOGLE_USER_EMAIL}],
            "mode": "single-user",
            "note": "Admin API not available — auditing only the configured user",
        }

    try:
        results = admin.users().list(
            domain=COMPANY_DOMAIN,
            maxResults=500,
            orderBy="email",
        ).execute()
        users = results.get("users", [])
        return {
            "users": [{"email": u["primaryEmail"], "name": u.get("name", {}).get("fullName", "")} for u in users],
            "mode": "org-wide",
            "total": len(users),
        }
    except Exception as e:
        return {
            "users": [{"email": GOOGLE_USER_EMAIL}],
            "mode": "single-user-fallback",
            "error": str(e),
        }


def search_files(query: str = "", mime_type: str = "", page_size: int = 100) -> dict[str, Any]:
    """Search Google Drive files. Returns file IDs, names, owners, sharing status."""
    svc = _get_drive_service()
    if svc is None:
        return {"error": "Drive API not available — check OAuth credentials in .env"}

    q_parts = []
    if query:
        q_parts.append(f"fullText contains '{query}'")
    if mime_type:
        q_parts.append(f"mimeType = '{mime_type}'")
    q_parts.append("trashed = false")
    q_str = " and ".join(q_parts)

    try:
        results = svc.files().list(
            q=q_str,
            pageSize=min(page_size, 1000),
            fields="nextPageToken, files(id, name, mimeType, owners, shared, sharingUser, "
                   "webViewLink, createdTime, modifiedTime, permissions(id, type, role, "
                   "emailAddress, domain, expirationTime, displayName))",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        files = results.get("files", [])
        return {
            "items": files,
            "total": len(files),
            "has_more": bool(results.get("nextPageToken")),
            "query": q_str,
        }
    except Exception as e:
        return {"error": str(e), "query": q_str}


def get_permissions(file_id: str) -> dict[str, Any]:
    """Get detailed sharing permissions for a specific file."""
    svc = _get_drive_service()
    if svc is None:
        return {"error": "Drive API not available"}

    try:
        file_meta = svc.files().get(
            fileId=file_id,
            fields="id, name, mimeType, owners, shared, webViewLink, "
                   "permissions(id, type, role, emailAddress, domain, "
                   "expirationTime, displayName, deleted)",
            supportsAllDrives=True,
        ).execute()
        return {"file": file_meta}
    except Exception as e:
        return {"error": str(e), "file_id": file_id}


def check_file_risks(file_meta: dict) -> list[dict[str, Any]]:
    """Analyze a file's permissions and return a list of risks with severity."""
    risks = []
    name = file_meta.get("name", "").lower()
    permissions = file_meta.get("permissions", [])

    is_sensitive = any(p in name for p in SENSITIVE_PATTERNS)
    is_credential = any(p in name for p in ["password", "credential", "api key", "secret", "token"])

    for perm in permissions:
        ptype = perm.get("type", "")
        role = perm.get("role", "")
        email = perm.get("emailAddress", "")
        domain = perm.get("domain", "")
        expiry = perm.get("expirationTime")

        if ptype == "anyone":
            if is_credential:
                risks.append({
                    "severity": "CRITICAL",
                    "risk": "Credentials/secrets publicly accessible",
                    "detail": f"File '{file_meta.get('name')}' contains credential-related data and is shared with anyone",
                    "permission_id": perm.get("id"),
                    "role": role,
                })
            elif is_sensitive:
                risks.append({
                    "severity": "CRITICAL",
                    "risk": "Sensitive file publicly shared",
                    "detail": f"File '{file_meta.get('name')}' matches sensitive pattern and has 'anyone with link' access",
                    "permission_id": perm.get("id"),
                    "role": role,
                })
            else:
                risks.append({
                    "severity": "MEDIUM",
                    "risk": "File has public link sharing",
                    "detail": f"File '{file_meta.get('name')}' is accessible to anyone with the link ({role})",
                    "permission_id": perm.get("id"),
                    "role": role,
                })

        elif ptype == "domain" and domain and domain != COMPANY_DOMAIN:
            risks.append({
                "severity": "MEDIUM",
                "risk": "Shared with external domain",
                "detail": f"File '{file_meta.get('name')}' is shared with entire domain '{domain}' ({role})",
                "permission_id": perm.get("id"),
                "domain": domain,
                "role": role,
            })

        elif ptype == "user" and email and not email.endswith(f"@{COMPANY_DOMAIN}"):
            severity = "HIGH"
            if is_sensitive:
                severity = "HIGH"
            if role in ("writer", "owner"):
                severity = "HIGH"
                risk_msg = "External user has editor access"
            else:
                risk_msg = "File shared with external user"

            risks.append({
                "severity": severity,
                "risk": risk_msg,
                "detail": f"File '{file_meta.get('name')}' shared with external user '{email}' ({role})",
                "permission_id": perm.get("id"),
                "email": email,
                "role": role,
            })

            if not expiry:
                risks.append({
                    "severity": "MEDIUM",
                    "risk": "External share has no expiration",
                    "detail": f"Share with '{email}' on '{file_meta.get('name')}' has no expiration date",
                    "email": email,
                })

        elif ptype == "group" and email and not email.endswith(f"@{COMPANY_DOMAIN}"):
            risks.append({
                "severity": "HIGH",
                "risk": "Shared with external group",
                "detail": f"File '{file_meta.get('name')}' shared with external group '{email}' ({role})",
                "permission_id": perm.get("id"),
                "email": email,
                "role": role,
            })

    return risks


def list_shared_files(page_size: int = 200) -> dict[str, Any]:
    """List all files that are shared (have more than just the owner)."""
    svc = _get_drive_service()
    if svc is None:
        return {"error": "Drive API not available"}

    try:
        results = svc.files().list(
            q="sharedWithMe = true or shared = true",
            pageSize=min(page_size, 1000),
            fields="nextPageToken, files(id, name, mimeType, owners, shared, "
                   "webViewLink, modifiedTime, permissions(id, type, role, "
                   "emailAddress, domain, expirationTime, displayName))",
            orderBy="modifiedTime desc",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        files = results.get("files", [])
        return {
            "items": files,
            "total": len(files),
            "has_more": bool(results.get("nextPageToken")),
        }
    except Exception as e:
        return {"error": str(e)}


def search_sensitive_files() -> dict[str, Any]:
    """Search for files matching sensitive name patterns."""
    svc = _get_drive_service()
    if svc is None:
        return {"error": "Drive API not available"}

    all_files = []
    for pattern in SENSITIVE_PATTERNS[:10]:
        try:
            results = svc.files().list(
                q=f"name contains '{pattern}' and trashed = false",
                pageSize=50,
                fields="files(id, name, mimeType, owners, shared, webViewLink, "
                       "modifiedTime, permissions(id, type, role, emailAddress, "
                       "domain, expirationTime, displayName))",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for f in results.get("files", []):
                if f["id"] not in {x["id"] for x in all_files}:
                    all_files.append(f)
        except Exception as e:
            logger.debug("Search for '%s' failed: %s", pattern, e)

    return {
        "items": all_files,
        "total": len(all_files),
        "patterns_searched": SENSITIVE_PATTERNS[:10],
    }


# ---------------------------------------------------------------------------
# Registry: all tools with metadata for ADK FunctionTool wrapping
# ---------------------------------------------------------------------------

ALL_TOOLS = {
    "drive.list_org_users": {
        "fn": list_org_users,
        "description": "List all users in the Google Workspace org for org-wide audit.",
    },
    "drive.search_files": {
        "fn": search_files,
        "description": "Search Google Drive files by query or mime type. Returns file metadata and permissions.",
    },
    "drive.get_permissions": {
        "fn": get_permissions,
        "description": "Get detailed sharing permissions for a specific file by ID.",
    },
    "drive.check_file_risks": {
        "fn": check_file_risks,
        "description": "Analyze a file's permissions and classify sharing risks by severity.",
    },
    "drive.list_shared_files": {
        "fn": list_shared_files,
        "description": "List all files that are shared with others (not just owner).",
    },
    "drive.search_sensitive_files": {
        "fn": search_sensitive_files,
        "description": "Search for files matching sensitive name patterns (contract, NDA, salary, credentials, etc.).",
    },
}
