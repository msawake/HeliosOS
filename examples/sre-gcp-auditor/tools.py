"""
GCP Read-Only Audit Tools — gcloud CLI wrappers for the SRE Daily Auditor.

Each function shells out to `gcloud ... --format=json` and returns structured
data. All commands are read-only (list, describe, get). The Helios OS kernel
gates every call via the ADK adapter — the manifest allowlist controls which
tools the agent may invoke.

Requires: gcloud CLI authenticated with an org-level viewer service account.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger("sre-gcp-auditor.tools")

_TIMEOUT = 60


def _run_gcloud(*args: str) -> dict[str, Any]:
    """Run a gcloud command and return parsed JSON."""
    cmd = ["gcloud", *args, "--format=json"]
    logger.debug("gcloud %s", " ".join(args))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip(), "command": " ".join(cmd)}
        raw = result.stdout.strip()
        if not raw:
            return {"items": []}
        return {"items": json.loads(raw)}
    except subprocess.TimeoutExpired:
        return {"error": f"gcloud timed out after {_TIMEOUT}s", "command": " ".join(cmd)}
    except Exception as exc:
        return {"error": str(exc), "command": " ".join(cmd)}


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def list_projects() -> dict[str, Any]:
    """List all GCP projects visible to the authenticated account."""
    return _run_gcloud("projects", "list")


def list_cloud_run_services(project_id: str) -> dict[str, Any]:
    """List Cloud Run services in a project with status and URL."""
    return _run_gcloud("run", "services", "list", f"--project={project_id}")


def list_cloud_sql_instances(project_id: str) -> dict[str, Any]:
    """List Cloud SQL instances — check state, IP config, backup status."""
    return _run_gcloud("sql", "instances", "list", f"--project={project_id}")


def list_gke_clusters(project_id: str) -> dict[str, Any]:
    """List GKE clusters — version, node count, autoscaling config."""
    return _run_gcloud("container", "clusters", "list", f"--project={project_id}")


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def list_firewall_rules(project_id: str) -> dict[str, Any]:
    """List VPC firewall rules — flag 0.0.0.0/0 on sensitive ports."""
    return _run_gcloud("compute", "firewall-rules", "list", f"--project={project_id}")


def list_service_accounts(project_id: str) -> dict[str, Any]:
    """List service accounts — check for unused keys, over-privileged roles."""
    return _run_gcloud("iam", "service-accounts", "list", f"--project={project_id}")


def list_storage_buckets(project_id: str) -> dict[str, Any]:
    """List Cloud Storage buckets — check public access, versioning."""
    return _run_gcloud("storage", "buckets", "list", f"--project={project_id}")


def list_secrets(project_id: str) -> dict[str, Any]:
    """List Secret Manager secrets — check rotation, access policy."""
    return _run_gcloud("secrets", "list", f"--project={project_id}")


def list_iam_bindings(project_id: str) -> dict[str, Any]:
    """List IAM policy bindings — flag external members, broad roles."""
    return _run_gcloud("projects", "get-iam-policy", project_id)


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

def get_billing_info(project_id: str) -> dict[str, Any]:
    """Get billing account and budget info for a project."""
    return _run_gcloud("billing", "projects", "describe", project_id)


# ---------------------------------------------------------------------------
# Registry: all tools with metadata for ADK FunctionTool wrapping
# ---------------------------------------------------------------------------

ALL_TOOLS = {
    "gcp.list_projects": {
        "fn": list_projects,
        "description": "List all GCP projects in the organization.",
    },
    "gcp.list_cloud_run_services": {
        "fn": list_cloud_run_services,
        "description": "List Cloud Run services in a project with status and URL.",
    },
    "gcp.list_cloud_sql_instances": {
        "fn": list_cloud_sql_instances,
        "description": "List Cloud SQL instances — state, IP config, backup status.",
    },
    "gcp.list_gke_clusters": {
        "fn": list_gke_clusters,
        "description": "List GKE clusters — version, node count, autoscaling config.",
    },
    "gcp.list_firewall_rules": {
        "fn": list_firewall_rules,
        "description": "List VPC firewall rules — flag 0.0.0.0/0 on sensitive ports.",
    },
    "gcp.list_service_accounts": {
        "fn": list_service_accounts,
        "description": "List service accounts — unused keys, over-privileged roles.",
    },
    "gcp.list_storage_buckets": {
        "fn": list_storage_buckets,
        "description": "List Cloud Storage buckets — public access, versioning.",
    },
    "gcp.list_secrets": {
        "fn": list_secrets,
        "description": "List Secret Manager secrets — rotation, access policy.",
    },
    "gcp.list_iam_bindings": {
        "fn": list_iam_bindings,
        "description": "List IAM policy bindings — external members, broad roles.",
    },
    "gcp.get_billing_info": {
        "fn": get_billing_info,
        "description": "Get billing account and budget info for a project.",
    },
}
