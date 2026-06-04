"""
Risk & Compliance Auditor — shared scan + classification logic.

Used by both agent.py (governed) and agent_raw.py (ungoverned) so the *only*
difference between them is the ForgeOS runtime governance, not the audit logic.

The scan calls the real, in-repo `drive__audit_sharing` tool
(src/platform/drive_audit_tool.py), which uses the operator's FORGEOS_GWS_*
OAuth creds. When those creds are absent (e.g. a laptop without the GWS refresh
token), `scan()` returns a clearly-labeled SIMULATED dataset so the
governed-vs-ungoverned contrast is still demonstrable locally. Simulated runs
set `simulated: True` and must never be mistaken for a real audit.
"""

from __future__ import annotations

import os
from typing import Any

# Names that imply attorney-client privilege / confidentiality. A public file
# whose name matches these is a potential privilege waiver — the CRITICAL case.
PRIVILEGED_PATTERNS = [
    p.strip().lower()
    for p in os.environ.get(
        "RISK_PRIVILEGED_PATTERNS",
        "privileged,attorney-client,work product,confidential,settlement,"
        "nda,engagement letter,retainer,deposition,memo,complaint,deal room",
    ).split(",")
    if p.strip()
]

# A clearly-labeled fixture for offline demos. Mirrors the shape of a real
# drive__audit_sharing response (files[].permissions[].type/role).
_SIMULATED_FILES: list[dict[str, Any]] = [
    {
        "id": "sim-priv-001",
        "name": "PRIVILEGED — Acme v. Globex Settlement Memo.docx",
        "mimeType": "application/vnd.google-apps.document",
        "owners": [{"emailAddress": "associate@marburystone.example"}],
        "webViewLink": "https://drive.google.com/file/d/sim-priv-001/view",
        "permissions": [{"type": "anyone", "role": "reader", "allowFileDiscovery": False}],
    },
    {
        "id": "sim-eng-002",
        "name": "Engagement Letter — Project Titan.pdf",
        "mimeType": "application/pdf",
        "owners": [{"emailAddress": "partner@marburystone.example"}],
        "webViewLink": "https://drive.google.com/file/d/sim-eng-002/view",
        "permissions": [{"type": "domain", "domain": "marburystone.example", "role": "reader"}],
    },
    {
        "id": "sim-mkt-003",
        "name": "Marbury & Stone — Firm Brochure 2026.pdf",
        "mimeType": "application/pdf",
        "owners": [{"emailAddress": "marketing@marburystone.example"}],
        "webViewLink": "https://drive.google.com/file/d/sim-mkt-003/view",
        "permissions": [{"type": "anyone", "role": "reader", "allowFileDiscovery": True}],
    },
]


def scan(max_files: int = 200) -> dict[str, Any]:
    """Enumerate over-shared Drive files. Real call first; labeled simulation
    fallback when GWS creds are unavailable."""
    try:
        from src.platform.drive_audit_tool import audit_sharing

        result = audit_sharing(max_files=max_files)
        if result.get("ok"):
            result["simulated"] = False
            return result
        # No/invalid creds — fall back to the labeled fixture for the demo.
        return {
            "ok": True,
            "simulated": True,
            "count": len(_SIMULATED_FILES),
            "files": list(_SIMULATED_FILES),
            "note": f"SIMULATED audit (real scan unavailable: {result.get('error', 'no creds')}).",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": True,
            "simulated": True,
            "count": len(_SIMULATED_FILES),
            "files": list(_SIMULATED_FILES),
            "note": f"SIMULATED audit (real scan raised: {e}).",
        }


def _is_public(perm: dict) -> bool:
    return perm.get("type") == "anyone"


def _is_domain(perm: dict) -> bool:
    return perm.get("type") == "domain"


def classify(files: list[dict]) -> list[dict[str, Any]]:
    """Classify each over-shared file into CRITICAL/HIGH/MEDIUM/LOW with a reason.

    CRITICAL — public AND privileged-named (potential privilege waiver).
    HIGH     — public (any name); or privileged-named shared whole-domain.
    MEDIUM   — non-privileged shared whole-domain.
    LOW      — public but clearly intended (firm marketing/brochure/template).
    """
    findings: list[dict[str, Any]] = []
    for f in files:
        name = (f.get("name") or "")
        lname = name.lower()
        perms = f.get("permissions") or []
        public = any(_is_public(p) for p in perms)
        domain = any(_is_domain(p) for p in perms)
        privileged = any(p in lname for p in PRIVILEGED_PATTERNS)
        intended = any(k in lname for k in ("brochure", "marketing", "template", "public", "press"))

        if public and privileged:
            sev, why = "CRITICAL", "privileged/confidential document shared by public link — possible privilege waiver"
        elif public and intended:
            sev, why = "LOW", "public, but the name indicates intentionally-public material"
        elif public:
            sev, why = "HIGH", "file shared by public link"
        elif domain and privileged:
            sev, why = "HIGH", "privileged/confidential document shared with the whole domain"
        elif domain:
            sev, why = "MEDIUM", "file shared with the whole domain"
        else:
            sev, why = "LOW", "shared beyond named recipients"

        findings.append(
            {
                "severity": sev,
                "name": name,
                "id": f.get("id"),
                "owner": (f.get("owners") or [{}])[0].get("emailAddress", "?"),
                "link": f.get("webViewLink", ""),
                "why": why,
            }
        )
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    findings.sort(key=lambda r: order.get(r["severity"], 9))
    return findings
