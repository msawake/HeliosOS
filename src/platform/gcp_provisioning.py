"""Auto-provision a dedicated GCP service account per agent.

When an agent manifest sets ``spec.drive.provision: true``, the platform creates
a dedicated service account in the current project (``GCP_PROJECT_ID``), grants
the Cloud Run runtime SA ``roles/iam.serviceAccountTokenCreator`` on it (so the
agent can impersonate it keylessly — see ``drive_tool.py``), and optionally
grants it read-only BigQuery. The SA is deleted when the agent is undeployed.

Auth mirrors ``drive_tool.py``: use the platform's own ADC
(``google.auth.default()``) with ``cloud-platform`` scope and call the IAM /
Cloud Resource Manager REST APIs directly with ``requests`` — no extra deps.

Requires the runtime SA to hold (on the project):
  * ``roles/iam.serviceAccountAdmin``          — create/delete SAs + setIamPolicy on SAs
  * ``roles/resourcemanager.projectIamAdmin``  — add the BigQuery project bindings
"""
from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

_IAM = "https://iam.googleapis.com/v1"
_CRM = "https://cloudresourcemanager.googleapis.com/v1"
_CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
_BQ_ROLES = ("roles/bigquery.jobUser", "roles/bigquery.dataViewer")
_TOKEN_CREATOR = "roles/iam.serviceAccountTokenCreator"

# GCP service-account id rule: 6–30 chars, start with a letter, end alphanumeric.
_ACCOUNT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class ProvisioningError(RuntimeError):
    """Raised when SA provisioning/deprovisioning cannot complete."""


def sanitize_account_id(slug: str) -> str:
    """Coerce an arbitrary slug into a valid GCP service-account id.

    Lowercase, non-[a-z0-9-] → '-', collapse repeats, ensure it starts with a
    letter and is 6–30 chars. Raises ProvisioningError if nothing usable remains.
    """
    s = re.sub(r"[^a-z0-9-]+", "-", (slug or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if s and not s[0].isalpha():
        s = "a-" + s
    if len(s) < 6:
        s = (s + "-agent")[:30] if s else "agent-sa"
    s = s[:30].rstrip("-")
    if len(s) < 6:
        s = (s + "-agent")[:30]
    if not _ACCOUNT_ID_RE.match(s):
        raise ProvisioningError(f"cannot derive a valid SA id from slug {slug!r} (got {s!r})")
    return s


def _access_token() -> str:
    try:
        from google.auth import default as google_default
        from google.auth.transport.requests import Request as _GoogleRequest
    except ImportError as e:  # pragma: no cover
        raise ProvisioningError(f"google-auth not installed: {e}") from e
    creds, _proj = google_default(scopes=[_CLOUD_PLATFORM])
    creds.refresh(_GoogleRequest())
    if not creds.token:
        raise ProvisioningError("could not obtain a cloud-platform access token from runtime ADC")
    return creds.token


def default_project_id() -> str:
    """Project the SAs are created in: ``GCP_PROJECT_ID`` env, else the project
    from the runtime ADC (``google.auth.default()`` — the running project on
    Cloud Run/GKE). Empty string if neither is available."""
    env = os.environ.get("GCP_PROJECT_ID", "").strip()
    if env:
        return env
    try:
        from google.auth import default as google_default
        _creds, project = google_default(scopes=[_CLOUD_PLATFORM])
        return (project or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _runtime_sa_email() -> str:
    """Email of the platform's own runtime identity (the impersonator)."""
    env = os.environ.get("FORGEOS_RUNTIME_SA_EMAIL", "").strip()
    if env:
        return env
    try:
        from google.auth import default as google_default
        creds, _ = google_default(scopes=[_CLOUD_PLATFORM])
        email = getattr(creds, "service_account_email", None)
        if email and email != "default":
            return email
    except Exception:  # noqa: BLE001
        pass
    # Metadata server fallback (Cloud Run / GCE).
    try:
        import requests
        r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"}, timeout=3,
        )
        if r.ok and r.text.strip():
            return r.text.strip()
    except Exception:  # noqa: BLE001
        pass
    raise ProvisioningError(
        "could not determine the runtime SA email (set FORGEOS_RUNTIME_SA_EMAIL)"
    )


def _hdrs(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def provision_agent_sa(
    slug: str, *, project_id: str, runtime_sa_email: str | None = None,
    grant_bigquery: bool = True,
) -> str:
    """Create (idempotently) a dedicated SA and wire it for agent use.

    Returns the created SA email. Steps: create SA → grant the runtime SA
    token-creator on it → (optionally) grant it project-level BigQuery read.
    """
    import requests
    if not project_id:
        raise ProvisioningError("project_id is required (set GCP_PROJECT_ID)")
    account_id = sanitize_account_id(slug)
    sa_email = f"{account_id}@{project_id}.iam.gserviceaccount.com"
    runtime_sa = runtime_sa_email or _runtime_sa_email()
    token = _access_token()

    # 1) Create the SA (409 = already exists → reuse).
    r = requests.post(
        f"{_IAM}/projects/{project_id}/serviceAccounts", headers=_hdrs(token),
        json={"accountId": account_id,
              "serviceAccount": {"displayName": f"Helios agent SA ({account_id})",
                                 "description": "Auto-provisioned by the Helios agent wizard"}},
        timeout=30,
    )
    if r.status_code not in (200, 201) and r.status_code != 409:
        raise ProvisioningError(f"create SA failed ({r.status_code}): {r.text[:300]}")

    # 2) Grant the runtime SA token-creator ON the new SA (so it can impersonate).
    _add_binding(
        f"{_IAM}/projects/{project_id}/serviceAccounts/{sa_email}",
        role=_TOKEN_CREATOR, member=f"serviceAccount:{runtime_sa}", token=token,
    )

    # 3) Optionally grant the new SA project-level BigQuery read.
    if grant_bigquery:
        for role in _BQ_ROLES:
            _add_binding(f"{_CRM}/projects/{project_id}", role=role,
                         member=f"serviceAccount:{sa_email}", token=token)

    logger.info("provisioned agent SA %s (runtime=%s, bq=%s)", sa_email, runtime_sa, grant_bigquery)
    return sa_email


def _add_binding(resource_url: str, *, role: str, member: str, token: str,
                 _attempts: int = 3) -> None:
    """get→modify→setIamPolicy add of (role, member) on a resource. Idempotent;
    retries on the concurrent-etag 409."""
    import requests
    for attempt in range(_attempts):
        g = requests.post(f"{resource_url}:getIamPolicy", headers=_hdrs(token),
                          json={"options": {"requestedPolicyVersion": 3}}, timeout=30)
        if not g.ok:
            raise ProvisioningError(f"getIamPolicy failed ({g.status_code}): {g.text[:300]}")
        policy = g.json() or {}
        policy.setdefault("bindings", [])
        b = next((x for x in policy["bindings"]
                  if x.get("role") == role and not x.get("condition")), None)
        if b is None:
            policy["bindings"].append({"role": role, "members": [member]})
        elif member in b.get("members", []):
            return  # already present
        else:
            b.setdefault("members", []).append(member)
        s = requests.post(f"{resource_url}:setIamPolicy", headers=_hdrs(token),
                          json={"policy": policy}, timeout=30)
        if s.ok:
            return
        if s.status_code == 409 and attempt < _attempts - 1:
            time.sleep(0.5 * (attempt + 1))
            continue
        raise ProvisioningError(f"setIamPolicy failed ({s.status_code}): {s.text[:300]}")


def deprovision_agent_sa(sa_email: str, *, project_id: str) -> bool:
    """Delete a provisioned SA. Returns True if deleted, False if already gone.

    Safety: callers must only pass SAs the platform created (``_drive.provisioned``).
    """
    import requests
    if not (sa_email and project_id):
        return False
    token = _access_token()
    r = requests.delete(
        f"{_IAM}/projects/{project_id}/serviceAccounts/{sa_email}",
        headers=_hdrs(token), timeout=30,
    )
    if r.status_code in (200, 204):
        logger.info("deprovisioned agent SA %s", sa_email)
        return True
    if r.status_code == 404:
        return False
    raise ProvisioningError(f"delete SA failed ({r.status_code}): {r.text[:300]}")
