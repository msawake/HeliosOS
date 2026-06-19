"""
Real GitHub provider.

Uses `PyGithub` when installed and `GITHUB_TOKEN` is set. Falls back to
returning an error dict when prerequisites are missing (the provider
system will prefer the simulated handler in that case, since `resolve()`
returns None from the loader).

Env:
    GITHUB_TOKEN                          (required)
    FORGEOS_ENABLE_REAL_GITHUB=1          (required)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_github_client():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")
    try:
        from github import Github  # type: ignore
    except ImportError as e:
        raise RuntimeError(f"PyGithub not installed: {e}")
    return Github(token)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_github_get_pr(tool_input: dict, agent_context: dict | None) -> dict:
    """Fetch real PR details via PyGithub."""
    repo = tool_input.get("repo", "")
    pr_number = tool_input.get("pr_number")
    if not repo or pr_number is None:
        return {"success": False, "error": "repo and pr_number are required"}

    try:
        gh = _get_github_client()
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    try:
        repository = gh.get_repo(repo)
        pr = repository.get_pull(int(pr_number))
    except Exception as e:
        return {"success": False, "error": f"get_pr failed: {e}"}

    labels = [l.name for l in pr.labels]
    reviewers = [r.login for r in pr.requested_reviewers] if pr.requested_reviewers else []

    return {
        "success": True,
        "repo": repo,
        "pr_number": pr.number,
        "title": pr.title,
        "state": pr.state,
        "author": pr.user.login if pr.user else None,
        "branch": pr.head.ref if pr.head else None,
        "base_branch": pr.base.ref if pr.base else None,
        "created_at": pr.created_at.isoformat() if pr.created_at else None,
        "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
        "description": pr.body or "",
        "files_changed": pr.changed_files,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "commits": pr.commits,
        "labels": labels,
        "reviewers": reviewers,
        "mergeable": pr.mergeable,
        "merged": pr.merged,
        "comments_count": pr.comments,
        "html_url": pr.html_url,
    }


def handle_github_create_review(tool_input: dict, agent_context: dict | None) -> dict:
    """Create a real review on a PR via PyGithub."""
    repo = tool_input.get("repo", "")
    pr_number = tool_input.get("pr_number")
    body = tool_input.get("body", "")
    event = tool_input.get("event", "COMMENT")  # APPROVE, REQUEST_CHANGES, COMMENT

    if not repo or pr_number is None:
        return {"success": False, "error": "repo and pr_number are required"}

    try:
        gh = _get_github_client()
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    try:
        repository = gh.get_repo(repo)
        pr = repository.get_pull(int(pr_number))
        review = pr.create_review(body=body, event=event)
    except Exception as e:
        return {"success": False, "error": f"create_review failed: {e}"}

    return {
        "success": True,
        "review_id": review.id if hasattr(review, "id") else None,
        "repo": repo,
        "pr_number": int(pr_number),
        "event": event,
        "body": body,
        "submitted_by": (agent_context or {}).get("agent_id", "unknown"),
        "submitted_at": _now_iso(),
    }
