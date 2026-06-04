#!/usr/bin/env python3
"""
Seed the Marbury & Stone LLP demo fixtures into Google Drive.

Creates a believable law-firm folder tree inside a parent folder that you have
shared with the drive-agent service account (Editor). Re-uses the platform's
keyless SA impersonation (src/platform/drive_tool.py) for auth — no key files.

Prereqs (same as examples/drive-chat-agent):
  - FORGEOS_DRIVE_AGENT_SA set to the drive-agent SA email (a default matching
    the proven drive-chat-agent SA is applied if unset).
  - Your ADC (gcloud auth application-default login) can impersonate that SA
    (roles/iam.serviceAccountTokenCreator), OR you run this where the platform's
    runtime SA can.
  - A Drive folder shared with the SA's email as Editor; pass its id with
    --folder-id. (The seeder creates "Marbury & Stone — Demo" *inside* it.)

Usage:
  PYTHONPATH=. python3 examples/law-firm/seed_drive.py --folder-id <PARENT_ID>
  PYTHONPATH=. python3 examples/law-firm/seed_drive.py --folder-id <PARENT_ID> --dry-run

On success it prints the demo root folder id — paste that into each agent's
manifest `metadata.firm_root_folder_id` (or just let the agents find it by name).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Default to the SA proven by examples/drive-chat-agent if the operator didn't set one.
os.environ.setdefault(
    "FORGEOS_DRIVE_AGENT_SA",
    "forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com",
)
os.environ.setdefault("FORGEOS_DRIVE_SCOPES", "drive")

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def _headers() -> dict:
    from src.platform.drive_tool import _auth_headers

    h = _auth_headers()
    if "Authorization" not in h:
        print(f"AUTH FAILED: {h}", file=sys.stderr)
        sys.exit(1)
    return h


def _create_folder(name: str, parent_id: str, *, dry: bool) -> str:
    if dry:
        print(f"  [dry] folder: {name}")
        return f"dry-{name}"
    import requests

    meta = {"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]}
    r = requests.post(
        _DRIVE_API,
        headers={**_headers(), "Content-Type": "application/json"},
        params={"supportsAllDrives": "true", "fields": "id,name"},
        data=json.dumps(meta),
        timeout=30,
    )
    if r.status_code not in (200, 201):
        print(f"  FAILED folder {name}: {r.status_code} {r.text[:200]}", file=sys.stderr)
        sys.exit(1)
    fid = r.json()["id"]
    print(f"  folder: {name}  ({fid})")
    return fid


def _create_file(name: str, parent_id: str, content: str, mime: str, *, dry: bool) -> str:
    if dry:
        print(f"    [dry] file: {name} [{mime}]")
        return f"dry-{name}"
    from src.platform.drive_tool import create_file

    res = create_file(name=name, content=content, folder_id=parent_id, mime_type=mime)
    if not res.get("ok"):
        err = res.get("error", "")
        print(f"    FAILED file {name}: {err}", file=sys.stderr)
        if "storageQuota" in err or "storage quota" in err.lower():
            print(
                "\n>>> The service account has no Drive storage of its own, so it cannot\n"
                ">>> OWN files in a regular My Drive folder. Put the parent folder in a\n"
                ">>> SHARED DRIVE and add the SA as a Content Manager, then pass that\n"
                ">>> folder's id with --folder-id. (Folders need no quota, which is why\n"
                ">>> the folder above was created but the file was rejected.)\n"
                ">>> See README.md § 'Google Drive setup' for the one-time steps.",
                file=sys.stderr,
            )
        sys.exit(1)
    print(f"    file: {name}  ({res.get('file_id')})")
    return res.get("file_id")


# --- Fixture content --------------------------------------------------------

CLIENTS_CSV = """Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Wayne Enterprises,Wayne Estate Planning,N/A,Active
Globex Industries,Globex IPO 2024,N/A,Closed (former client)
Daily Planet,Employment advisory,N/A,Active
"""

DOCKET_CSV = """Matter,Deadline Type,Due Date,Responsible Attorney,Notes
Globex IPO 2024,Document retention review,2026-05-28,M. Marbury,Annual review
Stark v. Hammer Tech,Reply brief,2026-06-03,A. Bergas,Opposition filed 2026-05-20
Acme v. Initech,Statute of limitations,2026-06-10,A. Bergas,4-year SOL on contract claim
Wayne Estate Planning,Discovery cutoff,2026-07-15,J. Stone,
"""

INTAKE_ACME = """# New Client Intake — Acme Corp

- **Prospective client:** Acme Corp
- **Matter:** Acme Corp v. Initech — breach of a software supply agreement
- **Adverse / opposing party:** Initech
- **Matter type:** Commercial litigation
- **Requested scope:** Pre-litigation demand, then complaint if unresolved
- **Proposed rate:** $650/hr (associate), $950/hr (partner)
- **Intake by:** reception, 2026-06-01

Notes: Acme says Initech missed delivery milestones and seeks damages + termination.
"""

INTAKE_HAMMER = """# New Client Intake — Hammer Tech

- **Prospective client:** Hammer Tech
- **Matter:** Hammer Tech v. Stark Industries — trade-secret dispute
- **Adverse / opposing party:** Stark Industries
- **Matter type:** IP litigation
- **Requested scope:** Defense + counterclaims
- **Proposed rate:** standard schedule
- **Intake by:** reception, 2026-06-01

Notes: Hammer Tech is adverse to Stark Industries.
"""

ENGAGEMENT_TEMPLATE = """# Engagement Letter — {{CLIENT}}

Dear {{CLIENT}},

Thank you for retaining **Marbury & Stone LLP** in connection with **{{MATTER}}**.
This letter confirms the terms of our engagement.

## Scope of Representation
{{SCOPE}}

## Fees
Our fees for this matter will be billed at {{RATE}}, plus reasonable costs.

## Conflicts
We have completed a conflicts check and are not aware of any conflict that would
prevent this representation.

## Acceptance
Please countersign below to confirm these terms.

Sincerely,
Marbury & Stone LLP

_______________________________
Client signature / date
"""

MSA = """# Master Services Agreement — Project Titan

This Master Services Agreement ("Agreement") is entered into between
TitanCo, Inc. ("Customer") and Vendor Systems LLC ("Vendor").

1. Term. This Agreement begins on the Effective Date and continues for three (3)
   years, and AUTOMATICALLY RENEWS for successive one-year terms unless either
   party gives 30 days' notice.
2. Governing Law. (intentionally omitted in this draft)
3. Limitation of Liability. Vendor's aggregate liability is UNLIMITED for any
   breach of confidentiality obligations.
4. Indemnification. Customer shall indemnify Vendor against ANY and ALL claims
   arising from Customer's use of the services, without cap.
5. Termination. Vendor may terminate for convenience at any time on 10 days'
   notice; Customer may terminate only for cause.
6. Assignment. Either party may assign this Agreement, including on a change of
   control, without the other party's consent.
"""

NDA = """# Mutual Non-Disclosure Agreement — Project Titan

Between TitanCo, Inc. and Vendor Systems LLC.

1. Term. Confidentiality obligations survive for five (5) years from disclosure.
2. Governing Law. State of Delaware.
3. Remedies. Injunctive relief available; liability capped at fees paid.
4. Termination. Either party may terminate on 30 days' written notice.
"""

PRIV_MEMO = """# PRIVILEGED & CONFIDENTIAL — Attorney Work Product

Re: Acme v. Initech — settlement strategy

This memo reflects counsel's mental impressions and is protected by the
attorney-client privilege and the work-product doctrine. Do not distribute
outside the matter team.

Recommended opening position: $1.2M; walk-away floor: $750K.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Marbury & Stone demo fixtures into Drive.")
    ap.add_argument("--folder-id", required=True, help="Parent folder id shared with the SA (Editor).")
    ap.add_argument("--dry-run", action="store_true", help="Print the tree without creating anything.")
    args = ap.parse_args()
    dry = args.dry_run

    print(f"Seeding Marbury & Stone — Demo into parent {args.folder_id} "
          f"(SA={os.environ['FORGEOS_DRIVE_AGENT_SA']}){' [DRY RUN]' if dry else ''}")

    root = _create_folder("Marbury & Stone — Demo", args.folder_id, dry=dry)

    # Firm-wide sheets (native Google Sheets so they read back as CSV).
    _create_file("Clients & Matters.csv", root, CLIENTS_CSV, "application/vnd.google-apps.spreadsheet", dry=dry)
    _create_file("Docket & Deadlines.csv", root, DOCKET_CSV, "application/vnd.google-apps.spreadsheet", dry=dry)

    intake = _create_folder("Intake", root, dry=dry)
    _create_file("New Client — Acme Corp.md", intake, INTAKE_ACME, "text/markdown", dry=dry)
    _create_file("New Client — Hammer Tech.md", intake, INTAKE_HAMMER, "text/markdown", dry=dry)

    templates = _create_folder("Templates", root, dry=dry)
    _create_file("Engagement Letter.md", templates, ENGAGEMENT_TEMPLATE, "text/markdown", dry=dry)

    matters = _create_folder("Matters", root, dry=dry)
    acme = _create_folder("Acme v. Globex (Litigation)", matters, dry=dry)
    _create_file("PRIVILEGED — Settlement Strategy Memo.md", acme, PRIV_MEMO, "text/markdown", dry=dry)

    titan = _create_folder("Project Titan (M&A)", matters, dry=dry)
    deal_room = _create_folder("Deal Room", titan, dry=dry)
    _create_file("Master Services Agreement.md", deal_room, MSA, "text/markdown", dry=dry)
    _create_file("Mutual NDA.md", deal_room, NDA, "text/markdown", dry=dry)

    print()
    print(f"DONE. Firm root folder id: {root}")
    print("Paste that id into each agent manifest's metadata.firm_root_folder_id,")
    print("or just tell the associate to find 'Marbury & Stone — Demo'.")
    if not dry:
        print("\nTip: to demo the confidentiality auditor, open")
        print("  'PRIVILEGED — Settlement Strategy Memo.md' in Drive and set it to")
        print("  'Anyone with the link' so drive__audit_sharing surfaces a CRITICAL finding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
