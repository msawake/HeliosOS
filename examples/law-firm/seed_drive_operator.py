#!/usr/bin/env python3
"""
Seed the Marbury & Stone law-firm fixtures into Google Drive as the OPERATOR
(you), then share the root folder with the drive-agent service account so the
agents can read them.

Why operator-owned (not SA-owned): a service account has no Drive storage, so it
can't own files in a normal My Drive (storageQuotaExceeded). Creating them under
your account avoids that; sharing the folder with the SA grants the agents read
access — exactly the firm's authorization model.

Prereq (one-time): add the Drive scope to your ADC, then run this:
  gcloud auth application-default login \\
    --scopes=openid,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive
  PYTHONPATH=. python3 examples/law-firm/seed_drive_operator.py
"""
from __future__ import annotations

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seed_drive import (  # noqa: E402  (reuse the same fixture content)
    CLIENTS_CSV, DOCKET_CSV, ENGAGEMENT_TEMPLATE, INTAKE_ACME, INTAKE_HAMMER,
    MSA, NDA, PRIV_MEMO,
)

SA_EMAIL = os.environ.get(
    "FORGEOS_DRIVE_AGENT_SA",
    "forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com",
)
_API = "https://www.googleapis.com/drive/v3/files"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
_FOLDER = "application/vnd.google-apps.folder"
_SHEET = "application/vnd.google-apps.spreadsheet"


def _token() -> str:
    import google.auth
    import google.auth.transport.requests as greq

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive"])
    creds.refresh(greq.Request())
    if not creds.token:
        sys.exit("could not obtain a Drive token — re-run the gcloud ADC login with the drive scope")
    return creds.token


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _folder(tok: str, name: str, parent: str | None) -> str:
    meta = {"name": name, "mimeType": _FOLDER}
    if parent:
        meta["parents"] = [parent]
    r = requests.post(_API, headers={**_h(tok), "Content-Type": "application/json"},
                      params={"fields": "id"}, data=json.dumps(meta), timeout=30)
    r.raise_for_status()
    fid = r.json()["id"]
    print(f"  folder: {name}")
    return fid


def _file(tok: str, name: str, parent: str, content: str, mime: str, source_mime: str) -> str:
    meta = {"name": name, "mimeType": mime, "parents": [parent]}
    boundary = "===seed==="
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {source_mime}\r\n\r\n"
        f"{content}\r\n--{boundary}--"
    ).encode()
    r = requests.post(_UPLOAD, headers={**_h(tok), "Content-Type": f"multipart/related; boundary={boundary}"},
                      params={"uploadType": "multipart", "fields": "id"}, data=body, timeout=60)
    r.raise_for_status()
    print(f"    file: {name}")
    return r.json()["id"]


def _share(tok: str, file_id: str, email: str) -> None:
    r = requests.post(f"{_API}/{file_id}/permissions",
                      headers={**_h(tok), "Content-Type": "application/json"},
                      params={"sendNotificationEmail": "false"},
                      data=json.dumps({"type": "user", "role": "reader", "emailAddress": email}), timeout=30)
    r.raise_for_status()


def main() -> int:
    tok = _token()
    print(f"Creating 'Marbury & Stone — Demo' in your Drive, sharing with {SA_EMAIL} ...")
    root = _folder(tok, "Marbury & Stone — Demo", None)

    _file(tok, "Clients & Matters", root, CLIENTS_CSV, _SHEET, "text/csv")
    _file(tok, "Docket & Deadlines", root, DOCKET_CSV, _SHEET, "text/csv")

    intake = _folder(tok, "Intake", root)
    _file(tok, "New Client — Acme Corp.md", intake, INTAKE_ACME, "text/markdown", "text/markdown")
    _file(tok, "New Client — Hammer Tech.md", intake, INTAKE_HAMMER, "text/markdown", "text/markdown")

    templates = _folder(tok, "Templates", root)
    _file(tok, "Engagement Letter.md", templates, ENGAGEMENT_TEMPLATE, "text/markdown", "text/markdown")

    matters = _folder(tok, "Matters", root)
    acme = _folder(tok, "Acme v. Globex (Litigation)", matters)
    _file(tok, "PRIVILEGED — Settlement Strategy Memo.md", acme, PRIV_MEMO, "text/markdown", "text/markdown")
    titan = _folder(tok, "Project Titan (M&A)", matters)
    deal = _folder(tok, "Deal Room", titan)
    _file(tok, "Master Services Agreement.md", deal, MSA, "text/markdown", "text/markdown")
    _file(tok, "Mutual NDA.md", deal, NDA, "text/markdown", "text/markdown")

    # Share the whole tree with the SA (folder share cascades to children).
    _share(tok, root, SA_EMAIL)
    print()
    print(f"DONE. Shared with the agents (SA). Folder id: {root}")
    print(f"Open: https://drive.google.com/drive/folders/{root}")
    print("Now the agents can: \"find and read the files in 'Marbury & Stone — Demo'\".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
