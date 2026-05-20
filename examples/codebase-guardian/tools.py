"""
GitHub Read-Only Tools for the Codebase Guardian.

Uses the `gh` CLI (GitHub CLI) for repo operations. All commands are read-only.
The ForgeOS kernel gates every tool call via the ADK adapter wrapper.

Requires: `gh` CLI authenticated (`gh auth login`).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger("codebase-guardian.tools")

REPO = os.environ.get("GITHUB_REPO", "")
_TIMEOUT = 30


def _run_gh(*args: str) -> dict[str, Any]:
    """Run a gh CLI command and return parsed JSON."""
    cmd = ["gh", *args]
    if REPO and "--repo" not in args and "-R" not in args:
        cmd.extend(["--repo", REPO])
    logger.debug("gh %s", " ".join(args))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
        if result.returncode != 0:
            return {"error": result.stderr.strip(), "command": " ".join(cmd)}
        raw = result.stdout.strip()
        if not raw:
            return {"items": []}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return {"items": parsed}
            return parsed
        except json.JSONDecodeError:
            return {"text": raw}
    except FileNotFoundError:
        return {"error": "gh CLI not found — install from https://cli.github.com"}
    except subprocess.TimeoutExpired:
        return {"error": f"gh timed out after {_TIMEOUT}s"}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# PR Tools
# ---------------------------------------------------------------------------

def list_open_prs(limit: int = 20) -> dict[str, Any]:
    """List open pull requests with author, title, labels, and changed files count."""
    return _run_gh("pr", "list", "--state=open", f"--limit={limit}",
                   "--json=number,title,author,labels,createdAt,updatedAt,headRefName,changedFiles,additions,deletions")


def get_pr_details(pr_number: int) -> dict[str, Any]:
    """Get full details of a specific PR including body, reviews, and checks."""
    return _run_gh("pr", "view", str(pr_number),
                   "--json=number,title,author,body,labels,createdAt,updatedAt,headRefName,"
                   "changedFiles,additions,deletions,files,reviews,statusCheckRollup,mergeable")


def get_pr_diff(pr_number: int) -> dict[str, Any]:
    """Get the full diff of a PR."""
    result = _run_gh("pr", "diff", str(pr_number))
    if "error" in result:
        return result
    return {"diff": result.get("text", ""), "pr_number": pr_number}


def list_pr_files(pr_number: int, per_page: int = 30) -> dict[str, Any]:
    """List files changed in a PR (works for large PRs where diff API fails)."""
    return _run_gh("api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/files?per_page={per_page}")


def list_pr_comments(pr_number: int) -> dict[str, Any]:
    """List review comments on a PR."""
    return _run_gh("api", f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
                   "--jq=.[].body")


# ---------------------------------------------------------------------------
# Commit Tools
# ---------------------------------------------------------------------------

def list_recent_commits(limit: int = 20, since: str = "") -> dict[str, Any]:
    """List recent commits on the default branch."""
    args = ["api", "repos/{owner}/{repo}/commits", f"--jq=.[:${limit}]"]
    if since:
        args = ["api", f"repos/{{owner}}/{{repo}}/commits?since={since}&per_page={limit}"]
    return _run_gh(*args)


def get_commit_details(sha: str) -> dict[str, Any]:
    """Get full details of a commit including files changed and diff stats."""
    return _run_gh("api", f"repos/{{owner}}/{{repo}}/commits/{sha}")


# ---------------------------------------------------------------------------
# Security Tools
# ---------------------------------------------------------------------------

def list_security_alerts() -> dict[str, Any]:
    """List Dependabot and code scanning alerts."""
    dependabot = _run_gh("api", "repos/{owner}/{repo}/dependabot/alerts?state=open&per_page=20")
    code_scanning = _run_gh("api", "repos/{owner}/{repo}/code-scanning/alerts?state=open&per_page=20")
    return {
        "dependabot": dependabot.get("items", []) if isinstance(dependabot, dict) else [],
        "code_scanning": code_scanning.get("items", []) if isinstance(code_scanning, dict) else [],
    }


def check_branch_protection(branch: str = "main") -> dict[str, Any]:
    """Check branch protection rules."""
    return _run_gh("api", f"repos/{{owner}}/{{repo}}/branches/{branch}/protection")


# ---------------------------------------------------------------------------
# Issue Tools
# ---------------------------------------------------------------------------

def list_open_issues(limit: int = 20) -> dict[str, Any]:
    """List open issues."""
    return _run_gh("issue", "list", "--state=open", f"--limit={limit}",
                   "--json=number,title,author,labels,createdAt,updatedAt")


def search_code(query: str) -> dict[str, Any]:
    """Search code in the repository."""
    return _run_gh("search", "code", query, "--json=path,repository,textMatches", "--limit=20")


# ---------------------------------------------------------------------------
# Workflow / CI Tools
# ---------------------------------------------------------------------------

def list_workflow_runs(limit: int = 10) -> dict[str, Any]:
    """List recent GitHub Actions workflow runs."""
    return _run_gh("run", "list", f"--limit={limit}",
                   "--json=databaseId,name,status,conclusion,headBranch,createdAt,updatedAt")


def get_failed_runs(limit: int = 5) -> dict[str, Any]:
    """List recent failed workflow runs with details."""
    return _run_gh("run", "list", "--status=failure", f"--limit={limit}",
                   "--json=databaseId,name,status,conclusion,headBranch,createdAt,url")


# ---------------------------------------------------------------------------
# Security Pattern Detection (local analysis, no API)
# ---------------------------------------------------------------------------

SECURITY_PATTERNS = [
    {"pattern": "password", "severity": "HIGH", "description": "Hardcoded password"},
    {"pattern": "api_key", "severity": "HIGH", "description": "Hardcoded API key"},
    {"pattern": "secret", "severity": "HIGH", "description": "Hardcoded secret"},
    {"pattern": "private_key", "severity": "CRITICAL", "description": "Private key in code"},
    {"pattern": "BEGIN RSA", "severity": "CRITICAL", "description": "RSA key in code"},
    {"pattern": "BEGIN OPENSSH", "severity": "CRITICAL", "description": "SSH key in code"},
    {"pattern": "eval(", "severity": "HIGH", "description": "eval() usage — potential code injection"},
    {"pattern": "exec(", "severity": "MEDIUM", "description": "exec() usage — potential code injection"},
    {"pattern": "subprocess.call(", "severity": "MEDIUM", "description": "Unvalidated subprocess call"},
    {"pattern": "shell=True", "severity": "HIGH", "description": "Shell injection risk"},
    {"pattern": "SELECT.*FROM.*WHERE", "severity": "MEDIUM", "description": "Raw SQL — potential injection"},
    {"pattern": "innerHTML", "severity": "HIGH", "description": "innerHTML — potential XSS"},
    {"pattern": "dangerouslySetInnerHTML", "severity": "HIGH", "description": "React XSS risk"},
    {"pattern": "disable_ssl", "severity": "HIGH", "description": "SSL verification disabled"},
    {"pattern": "verify=False", "severity": "HIGH", "description": "SSL verification disabled"},
    {"pattern": "CORS(app, origins='*')", "severity": "MEDIUM", "description": "CORS wildcard"},
    {"pattern": "0.0.0.0", "severity": "MEDIUM", "description": "Binding to all interfaces"},
    {"pattern": "--no-verify", "severity": "MEDIUM", "description": "Git hooks bypassed"},
]


def scan_diff_for_security(diff_text: str) -> list[dict[str, Any]]:
    """Scan a PR diff for security patterns. Returns list of findings."""
    findings = []
    for i, line in enumerate(diff_text.split("\n")):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        line_lower = line.lower()
        for pattern in SECURITY_PATTERNS:
            if pattern["pattern"].lower() in line_lower:
                findings.append({
                    "severity": pattern["severity"],
                    "pattern": pattern["pattern"],
                    "description": pattern["description"],
                    "line": line.strip()[:200],
                    "line_number": i,
                })
    return findings


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_TOOLS = {
    "github.list_open_prs": {
        "fn": list_open_prs,
        "description": "List open pull requests with metadata.",
    },
    "github.get_pr_details": {
        "fn": get_pr_details,
        "description": "Get full PR details including files, reviews, and CI status.",
    },
    "github.get_pr_diff": {
        "fn": get_pr_diff,
        "description": "Get the full diff of a pull request.",
    },
    "github.list_pr_comments": {
        "fn": list_pr_comments,
        "description": "List review comments on a PR.",
    },
    "github.list_recent_commits": {
        "fn": list_recent_commits,
        "description": "List recent commits on the default branch.",
    },
    "github.get_commit_details": {
        "fn": get_commit_details,
        "description": "Get full commit details including changed files.",
    },
    "github.list_security_alerts": {
        "fn": list_security_alerts,
        "description": "List Dependabot and code scanning security alerts.",
    },
    "github.check_branch_protection": {
        "fn": check_branch_protection,
        "description": "Check branch protection rules on main/master.",
    },
    "github.list_open_issues": {
        "fn": list_open_issues,
        "description": "List open GitHub issues.",
    },
    "github.list_workflow_runs": {
        "fn": list_workflow_runs,
        "description": "List recent GitHub Actions workflow runs.",
    },
    "github.get_failed_runs": {
        "fn": get_failed_runs,
        "description": "List recent failed CI workflow runs.",
    },
    "github.scan_diff_for_security": {
        "fn": scan_diff_for_security,
        "description": "Scan a PR diff for security patterns (hardcoded secrets, injection, XSS).",
    },
}
