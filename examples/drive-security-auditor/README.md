# Google Drive Security Auditor

Daily read-only audit of Google Drive sharing permissions across the org.
Flags public files, external sharing, credential exposure, and overly broad access.

Uses **ADK** + **Helios OS HTTP Kernel** (Mode C) for governance.

## What It Detects

| Severity | Risk | Example |
|----------|------|---------|
| **CRITICAL** | Publicly shared credentials | `api-keys.txt` with "anyone with link" |
| **CRITICAL** | Publicly shared sensitive docs | `Client NDA - Acme Corp.docx` visible to internet |
| **HIGH** | External user with editor access | `competitor@gmail.com` can edit company strategy doc |
| **HIGH** | Sensitive file shared externally | `2026 Salary Review.xlsx` shared with external consultant |
| **MEDIUM** | Public link on any file | `Meeting Notes.doc` with "anyone with link" (viewer) |
| **MEDIUM** | External share without expiration | Shared with contractor, no auto-revoke date |
| **LOW** | Editors can reshare | `writersCanShare` enabled on sensitive folders |

## Read-Only Enforcement (3 Layers)

1. **Code**: `tools.py` only calls `files().list()` and `permissions().list()` — no write methods
2. **OAuth**: `drive.readonly` scope — Google API rejects any write attempt
3. **Kernel**: Manifest explicitly denies `share_file`, `remove_permission`, `set_permissions`

## Prerequisites

```bash
pip install google-api-python-client google-auth 'google-adk[extensions]'
```

### Single-User Mode (quick start)
Uses existing OAuth credentials from `.env`:
```
GOOGLE_WORKSPACE_CLIENT_ID=...
GOOGLE_WORKSPACE_CLIENT_SECRET=...
GOOGLE_WORKSPACE_REFRESH_TOKEN=...
```

### Org-Wide Mode (full audit)
Requires a service account with domain-wide delegation:
```bash
# 1. Create service account
gcloud iam service-accounts create drive-auditor --project=YOUR_PROJECT

# 2. Enable domain-wide delegation in Admin Console:
#    Security > API controls > Domain-wide delegation
#    Add client ID with scopes:
#    - https://www.googleapis.com/auth/drive.readonly
#    - https://www.googleapis.com/auth/admin.directory.user.readonly

# 3. Set environment variables
export GOOGLE_SA_KEY_FILE=/path/to/sa-key.json
export GOOGLE_ADMIN_EMAIL=admin@yourdomain.com
export COMPANY_DOMAIN=yourdomain.com
```

## Usage

### Local (no governance)
```bash
PYTHONPATH=. python3 examples/drive-security-auditor/agent.py
```

### With Helios OS HTTP Kernel
```bash
FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
FORGEOS_AGENT_ID=drive-security-auditor \
ATLAS_GATEWAY_URL=https://atlas-gateway-xxx.run.app/v1 \
ATLAS_GATEWAY_KEY=sk-... \
PYTHONPATH=. python3 examples/drive-security-auditor/agent.py
```

## Report Output

Reports saved to `examples/drive-security-auditor/reports/drive-audit-YYYY-MM-DD.md`

## Runtime Governance Calls

| # | Call | Purpose |
|---|------|---------|
| 1 | `pending_signals()` | Check for drain/quarantine |
| 2 | `budget()` | Enough budget for today? |
| 3 | `process()` | Am I still RUNNING? |
| 4-N | `check_tool("drive.*")` | Kernel gate per Drive API call |
| N+1 | `audit("drive_audit.completed")` | Record results |
| N+2 | `checkpoint({date, findings})` | Save for crash recovery |
