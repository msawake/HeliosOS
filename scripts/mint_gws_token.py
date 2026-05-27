#!/usr/bin/env python3
"""Mint a fresh Gmail refresh token for the existing FORGEOS_GWS OAuth client
and write it back to Secret Manager.

The refresh token in `FORGEOS_GWS_REFRESH_TOKEN` is expired/revoked
(invalid_grant), which breaks every Gmail-send path. This re-runs the OAuth
consent flow using the *same* client_id/secret (so nothing else changes),
verifies the new token can actually call the Gmail API, then overwrites only
the refresh-token secret.

Usage:
    pip install google-auth-oauthlib requests google-cloud-secret-manager
    python3 scripts/mint_gws_token.py            # opens a browser to consent

It opens a local browser, you log in as the account that should SEND the
audit emails (e.g. antoni.bergas@makingscience.com or a shared sender), grant
the Gmail scope, and the script does the rest.
"""
from __future__ import annotations

import sys

PROJECT = "admachina-atomic-test-84"
# gmail.send → auditors email their reports.
# drive.metadata.readonly → drive-security-auditor lists file sharing/perms
#   (metadata only; it cannot read file *contents* with this scope).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


def _sm():
    from google.cloud import secretmanager
    return secretmanager.SecretManagerServiceClient()


def _get_secret(client, name: str) -> str:
    path = f"projects/{PROJECT}/secrets/{name}/versions/latest"
    return client.access_secret_version(name=path).payload.data.decode("utf-8").strip()


def _add_secret_version(client, name: str, value: str) -> None:
    parent = f"projects/{PROJECT}/secrets/{name}"
    client.add_secret_version(
        parent=parent, payload={"data": value.encode("utf-8")}
    )


def main() -> int:
    sm = _sm()
    client_id = _get_secret(sm, "FORGEOS_GWS_CLIENT_ID")
    client_secret = _get_secret(sm, "FORGEOS_GWS_CLIENT_SECRET")
    print(f"Using OAuth client {client_id[:24]}…")

    from google_auth_oauthlib.flow import InstalledAppFlow

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    # access_type=offline + prompt=consent forces a *refresh* token to be issued.
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent", open_browser=True
    )
    if not creds.refresh_token:
        print("ERROR: no refresh_token returned. Re-run; ensure you consent.", file=sys.stderr)
        return 1

    # Verify the token and show the granted scopes + account before storing.
    import requests
    info = requests.get(
        "https://www.googleapis.com/oauth2/v3/tokeninfo",
        params={"access_token": creds.token},
        timeout=20,
    )
    if info.status_code != 200:
        print(f"ERROR: tokeninfo check failed: {info.status_code} {info.text}", file=sys.stderr)
        return 1
    j = info.json()
    addr = j.get("email", "?")
    scopes = j.get("scope", "")
    print(f"✓ Token works. Account: {addr}")
    print(f"  Scopes: {scopes}")
    if "gmail.send" not in scopes:
        print("WARNING: gmail.send scope missing — email sending will fail.", file=sys.stderr)

    _add_secret_version(sm, "FORGEOS_GWS_REFRESH_TOKEN", creds.refresh_token)
    print("✓ Wrote new FORGEOS_GWS_REFRESH_TOKEN to Secret Manager.")
    print(f"\nSender for audit emails will be: {addr}")
    print("Done. Tell Claude the sender address so it can finish wiring the email tool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
