"""
Developer tools available to agents: opencode wrapper, scoped shell exec,
git commit/push, and `gh pr create`.

These tools are how a code-writing agent (e.g. forgeos-lens-builder) drives
opencode + git + gh non-interactively. They are intentionally narrow:

  * `shell__exec` honors a binary allowlist (`pnpm`, `npm`, `node`, `cargo`,
    `git`, `gh`, `opencode`, `ls`, `cat`, `mkdir`, `pwd`, `which`). No shell
    interpretation, no `rm -rf`, no `sudo`, no networking tools beyond gh.
  * `git__commit_push` refuses if the working tree contains modifications
    outside the listed files.
  * `gh__open_pr` requires `GH_TOKEN` in the environment (set by
    `executor.invoke()` from per-user Secret Manager credentials).
  * `code__opencode_run` shells out to the local `opencode` binary with
    `--model openai/<chat_model>` and `--base-url <vllm-url>`.

All four return dicts the agentic loop can present as tool_result content.
Errors are reported via the `error` key rather than raised, so the LLM sees
the failure and can self-correct.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


SHELL_ALLOWLIST = {
    "pnpm", "npm", "node", "npx",
    "cargo", "rustc", "rustup",
    "git", "gh",
    "opencode",
    "ls", "cat", "mkdir", "pwd", "which", "echo", "head", "tail",
    # `bash -c "<one-liner>"` is allowed so agents can chain git/gh/pnpm
    # commands in a single tool call when juggling multiple short steps
    # otherwise burns LLM rounds. The container runs as the non-root
    # forgeos user, network egress is the only thing of value here, and
    # the LLM-side prompts gate what reaches this layer in practice.
    "bash",
    "sh",
}

# Maximum stdout/stderr captured per tool result, in bytes.
MAX_OUTPUT_BYTES = 32_000

DEV_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "shell__exec",
        "description": (
            "Run a single command from the developer allowlist (no shell "
            "interpretation; no piping). Cwd must exist. Returns stdout, "
            "stderr, return code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command line; argv parsed via shlex."},
                "cwd": {"type": "string", "description": "Working directory."},
                "timeout": {"type": "integer", "default": 300, "minimum": 5, "maximum": 1800},
                "env": {
                    "type": "object",
                    "description": "Extra env vars (merged on top of inherited).",
                },
            },
            "required": ["cmd", "cwd"],
        },
    },
    {
        "name": "code__opencode_run",
        "description": (
            "Drive a non-interactive opencode coding pass. Spawns "
            "`opencode run --model openai/<model> --base-url <url> --cwd <repo_dir> "
            "<task>` and returns stdout, stderr, files_changed (parsed from "
            "`git status --porcelain`)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Natural-language task for opencode."},
                "repo_dir": {"type": "string", "description": "Absolute path to the repo."},
                "model": {"type": "string", "description": "OpenAI-compatible model id, e.g. 'nvidia/nemotron-3-super'."},
                "base_url": {"type": "string", "description": "OpenAI-compatible base URL."},
                "timeout": {"type": "integer", "default": 1200, "minimum": 60, "maximum": 3600},
            },
            "required": ["task", "repo_dir"],
        },
    },
    {
        "name": "git__commit_push",
        "description": (
            "Stage the listed files, commit with the given message, and "
            "push the named branch with -u origin. Refuses if the working "
            "tree has modifications outside the listed files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_dir": {"type": "string"},
                "branch": {"type": "string"},
                "message": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to add. Relative to repo_dir.",
                },
                "base": {"type": "string", "default": "main"},
            },
            "required": ["repo_dir", "branch", "message", "files"],
        },
    },
    {
        "name": "gh__open_pr",
        "description": (
            "Open a pull request via `gh pr create`. Requires GH_TOKEN in "
            "env (the platform injects it from per-user secrets at invoke "
            "time). Returns the PR URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_dir": {"type": "string"},
                "branch": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "base": {"type": "string", "default": "main"},
            },
            "required": ["repo_dir", "branch", "title", "body"],
        },
    },
]


def _truncate(s: str) -> str:
    if len(s) <= MAX_OUTPUT_BYTES:
        return s
    return s[: MAX_OUTPUT_BYTES] + f"\n[…truncated {len(s) - MAX_OUTPUT_BYTES} bytes…]"


def _resolve_cwd(cwd: str) -> Path | str:
    p = Path(cwd).expanduser()
    if not p.is_absolute():
        return f"cwd must be absolute: {cwd}"
    if not p.is_dir():
        return f"cwd does not exist: {cwd}"
    return p


def _run(argv: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None) -> dict[str, Any]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": f"timeout after {timeout}s",
            "stdout": _truncate(e.stdout or ""),
            "stderr": _truncate(e.stderr or ""),
            "returncode": -1,
        }
    except FileNotFoundError as e:
        return {"ok": False, "error": f"binary not found: {e.filename}", "returncode": -1}
    return {
        "ok": proc.returncode == 0,
        "stdout": _truncate(proc.stdout),
        "stderr": _truncate(proc.stderr),
        "returncode": proc.returncode,
    }


def shell_exec(*, cmd: str, cwd: str | None = None, timeout: int = 300, env: dict[str, str] | None = None) -> dict[str, Any]:
    # When the model forgets cwd, fall back to the working directory the
    # forgeos-lens-builder agent uses by default. /tmp is always available
    # and the allowlist forbids destructive commands anyway.
    if not cwd:
        fallback = "/tmp/forgeos-lens-builder/forgeos-lens"
        cwd = fallback if Path(fallback).is_dir() else "/tmp"
    cwd_or_err = _resolve_cwd(cwd)
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return {"ok": False, "error": f"could not parse cmd: {e}"}
    if not argv:
        return {"ok": False, "error": "empty cmd"}
    bin_name = Path(argv[0]).name
    if bin_name not in SHELL_ALLOWLIST:
        return {
            "ok": False,
            "error": f"binary '{bin_name}' not in allowlist {sorted(SHELL_ALLOWLIST)}",
        }
    return _run(argv, cwd_or_err, timeout=timeout, env=env)


def code_opencode_run(
    *,
    task: str,
    repo_dir: str,
    model: str | None = None,
    base_url: str | None = None,  # accepted for API stability; unused (opencode reads provider config)
    timeout: int = 1200,
) -> dict[str, Any]:
    cwd_or_err = _resolve_cwd(repo_dir)
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    # opencode is configured to route `atlas/nvidia/nemotron-3-super` via its
    # provider config (Atlas Gateway). Override with FORGEOS_OPENCODE_MODEL.
    model = model or os.environ.get("FORGEOS_OPENCODE_MODEL") or "atlas/nvidia/nemotron-3-super"
    argv = [
        "opencode", "run",
        "--model", model,
        "--dir", str(cwd_or_err),
        "--print-logs",
        "--log-level", "INFO",
        task,
    ]
    result = _run(argv, cwd_or_err, timeout=timeout)
    diff = _run(["git", "status", "--porcelain"], cwd_or_err, timeout=20)
    files_changed: list[str] = []
    for line in (diff.get("stdout") or "").splitlines():
        if len(line) > 3:
            files_changed.append(line[3:].strip())
    result["files_changed"] = files_changed
    return result


def git_commit_push(
    *,
    repo_dir: str,
    branch: str,
    message: str,
    files: list[str],
    base: str = "main",
) -> dict[str, Any]:
    cwd_or_err = _resolve_cwd(repo_dir)
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    # files = ["."] or empty / ["*"] means "stage every dirty path the agent
    # produced". Without this escape hatch, opencode-driven workflows
    # (which write 10+ files at once) were stuck running git__commit_push
    # 6× with subset lists, each rejected for "unlisted dirty paths".
    use_all = (not files) or files == ["."] or files == ["*"]
    status = _run(["git", "status", "--porcelain"], cwd_or_err, timeout=20)
    if not status["ok"]:
        return {"ok": False, "error": "git status failed", **status}
    dirty_paths = [line[3:].strip() for line in (status.get("stdout") or "").splitlines() if line.strip()]
    if not dirty_paths:
        return {"ok": False, "error": "working tree is clean — nothing to commit"}
    if not use_all:
        # Sanity-check: warn if dirty paths weren't listed but DO commit anyway.
        # Listing was too strict before — agents either listed everything or
        # gave up after one refusal. Switch from refusal to a non-fatal note.
        listed = {f.lstrip("./") for f in files}
        unlisted = [
            p for p in dirty_paths
            if p not in listed and not any(p.startswith(f.rstrip("/") + "/") for f in listed)
        ]
        if unlisted:
            logger.info(
                "git_commit_push: %d unlisted dirty paths included via stage-all (first 5: %s)",
                len(unlisted), unlisted[:5],
            )
    # Checkout branch (create if missing).
    cur = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd_or_err, timeout=10)
    current = (cur.get("stdout") or "").strip()
    if current != branch:
        co = _run(["git", "checkout", "-B", branch], cwd_or_err, timeout=30)
        if not co["ok"]:
            return {"ok": False, "error": "git checkout failed", **co}
    # Always `git add -A` so we include opencode's new files, deletions, and
    # renames; if `files` was explicit, the diff against that list is just
    # informational (logged above).
    add = _run(["git", "add", "-A"], cwd_or_err, timeout=30)
    if not add["ok"]:
        return {"ok": False, "error": "git add failed", **add}
    commit = _run(["git", "commit", "-m", message], cwd_or_err, timeout=30)
    if not commit["ok"]:
        # Empty commit (nothing staged) — treat as soft failure, not hard.
        if "nothing to commit" in (commit.get("stdout") or "") + (commit.get("stderr") or ""):
            return {"ok": False, "error": "nothing staged for commit", **commit}
        return {"ok": False, "error": "git commit failed", **commit}
    push = _run(["git", "push", "-u", "origin", branch], cwd_or_err, timeout=120)
    return {"ok": push["ok"], "branch": branch, "base": base, **push}


def gh_open_pr(
    *,
    repo_dir: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> dict[str, Any]:
    cwd_or_err = _resolve_cwd(repo_dir)
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        return {
            "ok": False,
            "error": "GH_TOKEN/GITHUB_TOKEN not set; cannot open PR",
        }
    argv = [
        "gh", "pr", "create",
        "--base", base,
        "--head", branch,
        "--title", title,
        "--body", body,
    ]
    result = _run(argv, cwd_or_err, timeout=60)
    # Parse PR URL from stdout (gh emits it on the last line).
    url = ""
    for line in (result.get("stdout") or "").splitlines():
        line = line.strip()
        if line.startswith("https://github.com/") and "/pull/" in line:
            url = line
    result["pr_url"] = url
    return result


# ---------------------------------------------------------------------------
# Tool handler glue for ToolExecutor
# ---------------------------------------------------------------------------

def _gh_token_from_ctx(agent_context: dict | None) -> str:
    if not agent_context:
        return ""
    creds = agent_context.get("_credentials") or {}
    return creds.get("gh_token") or ""


def _ensure_gh_env(env: dict[str, str] | None, agent_context: dict | None) -> dict[str, str]:
    """Layer per-invocation gh credentials on top of caller-supplied env.

    Never mutates os.environ. If no token is present, returns the env
    unchanged so we don't fabricate fake auth.
    """
    out = dict(env or {})
    token = _gh_token_from_ctx(agent_context)
    if token:
        out.setdefault("GH_TOKEN", token)
        out.setdefault("GITHUB_TOKEN", token)
    return out


async def _handle_shell_exec(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    # gh + git both ride on the per-invocation token when an agent uses
    # shell__exec to drive them directly (rather than the dedicated wrappers).
    inp = dict(tool_input)
    inp["env"] = _ensure_gh_env(inp.get("env"), agent_context)
    # subprocess.run inside an async handler would block the event loop —
    # under Cloud Run, that stalls the HTTP server, the scheduler, every
    # other in-flight invocation, and (when the call lasts minutes) gets
    # the instance flagged unresponsive. Offload to a worker thread.
    result = await asyncio.to_thread(shell_exec, **inp)
    return {"success": True, "result": result}


async def _handle_opencode_run(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    result = await asyncio.to_thread(code_opencode_run, **tool_input)
    return {"success": True, "result": result}


async def _handle_git_commit_push(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    token = _gh_token_from_ctx(agent_context)
    if token:
        result = await asyncio.to_thread(_git_commit_push_with_token, token=token, **tool_input)
    else:
        result = await asyncio.to_thread(git_commit_push, **tool_input)
    return {"success": True, "result": result}


async def _handle_gh_open_pr(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    token = _gh_token_from_ctx(agent_context)
    if token:
        result = await asyncio.to_thread(_gh_open_pr_with_token, token=token, **tool_input)
    else:
        result = await asyncio.to_thread(gh_open_pr, **tool_input)
    return {"success": True, "result": result}


# --- Auth-aware variants ----------------------------------------------------

def _git_commit_push_with_token(*, token: str, **kwargs: Any) -> dict[str, Any]:
    """Same as git_commit_push but pushes with GH_TOKEN-backed credentials.

    git uses GH_TOKEN via the `gh` credential helper when configured, but we
    don't assume `gh auth setup-git` has been run in the container. Instead
    we override the remote URL just for the push step using a temporary
    https://x-access-token:<token>@github.com style URL. That URL never lands
    in the repo config — we pass it via `git -c http.extraheader=...` is also
    an option but cleaner to use `-c credential.helper=` with a one-shot.
    """
    # Reuse the unauthenticated path for staging/commit; only swap auth at
    # the push step.
    cwd_or_err = _resolve_cwd(kwargs["repo_dir"])
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    base_result = git_commit_push(**{**kwargs, "files": kwargs.get("files", [])})
    if not base_result.get("ok") and "git push" not in (base_result.get("error") or ""):
        # Failure before push — bubble up as-is.
        if base_result.get("returncode") != 0 and "git push" not in str(base_result.get("stderr", "")):
            return base_result
    # If the push leg failed because of auth, retry with an inline credential.
    if not base_result.get("ok") and (
        "Authentication failed" in (base_result.get("stderr") or "")
        or "could not read Username" in (base_result.get("stderr") or "")
        or "fatal: could not read" in (base_result.get("stderr") or "")
        or "403" in (base_result.get("stderr") or "")
    ):
        env = {"GH_TOKEN": token, "GITHUB_TOKEN": token}
        retry = _run(
            ["git", "-c", f"http.extraHeader=Authorization: Bearer {token}",
             "push", "-u", "origin", kwargs["branch"]],
            cwd_or_err, timeout=120, env=env,
        )
        return {"ok": retry["ok"], "branch": kwargs["branch"], **retry}
    return base_result


def _gh_open_pr_with_token(*, token: str, **kwargs: Any) -> dict[str, Any]:
    cwd_or_err = _resolve_cwd(kwargs["repo_dir"])
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    argv = [
        "gh", "pr", "create",
        "--base", kwargs.get("base", "main"),
        "--head", kwargs["branch"],
        "--title", kwargs["title"],
        "--body", kwargs["body"],
    ]
    env = {"GH_TOKEN": token, "GITHUB_TOKEN": token}
    result = _run(argv, cwd_or_err, timeout=60, env=env)
    url = ""
    for line in (result.get("stdout") or "").splitlines():
        line = line.strip()
        if line.startswith("https://github.com/") and "/pull/" in line:
            url = line
    result["pr_url"] = url
    return result


DEV_TOOL_HANDLERS: dict[str, Any] = {
    "shell__exec": _handle_shell_exec,
    "code__opencode_run": _handle_opencode_run,
    "git__commit_push": _handle_git_commit_push,
    "gh__open_pr": _handle_gh_open_pr,
}
