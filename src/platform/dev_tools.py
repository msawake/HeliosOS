"""
Developer tools available to agents: qwen-code wrapper, scoped shell exec,
git commit/push, and `gh pr create`.

These tools are how a code-writing agent (e.g. forgeos-lens-builder) drives
qwen-code + git + gh non-interactively. They are intentionally narrow:

  * `shell__exec` honors a binary allowlist (`pnpm`, `npm`, `node`, `cargo`,
    `git`, `gh`, `qwen`, `ls`, `cat`, `mkdir`, `pwd`, `which`). No shell
    interpretation, no `rm -rf`, no `sudo`, no networking tools beyond gh.
  * `git__commit_push` refuses if the working tree contains modifications
    outside the listed files.
  * `gh__open_pr` requires `GH_TOKEN` in the environment (set by
    `executor.invoke()` from per-user Secret Manager credentials).
  * `code__qwen_code_run` shells out to the local `qwen` binary
    (https://github.com/QwenLM/qwen-code) with `--prompt`, `--yolo`, and
    `--output-format json`. The OpenAI-compatible endpoint is configured
    purely via env vars (`OPENAI_API_KEY`, `OPENAI_BASE_URL`,
    `OPENAI_MODEL`) so no `~/.qwen/settings.json` write is needed.

When a dev tool runs without an explicit `cwd`/`repo_dir`, it falls back
to a per-invocation workdir derived from `agent_context["invocation_id"]`
(e.g. `/tmp/forgeos-lens-builder/run-<inv>/forgeos-lens`) so two parallel
runs can't trample each other's git state.

All return dicts the agentic loop can present as tool_result content.
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
    # gcloud/gsutil/bq let read-only auditor agents (e.g. sre-gcp-auditor)
    # run `gcloud ... list/describe --format=json`. On Cloud Run these
    # authenticate via the metadata server (ADC) using the service account.
    "gcloud", "gsutil", "bq",
    "qwen",
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
        "name": "fs__write_file",
        "description": (
            "Write text to a file (creating parent dirs), overwriting unless "
            "append=true. Use this to author or edit source files and to "
            "compose multi-line bodies (e.g. a PR-review markdown file) — it is "
            "far more reliable than shell redirection, which shell__exec does "
            "NOT support (shell__exec runs a single binary with no '>' / '>>' / "
            "heredoc). Returns the resolved path and bytes written."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path; absolute, or relative to cwd."},
                "content": {"type": "string", "description": "Full file contents to write."},
                "cwd": {"type": "string", "description": "Base dir for a relative path."},
                "append": {"type": "boolean", "default": False, "description": "Append instead of overwrite."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "code__qwen_code_run",
        "description": (
            "Drive a non-interactive qwen-code coding pass. Spawns "
            "`qwen --prompt <task> --output-format json --yolo` with "
            "OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL env vars set so the "
            "CLI hits the configured OpenAI-compatible endpoint. Returns "
            "stdout, stderr, returncode, and files_changed (parsed from "
            "`git status --porcelain`). `repo_dir` is the working directory "
            "(omit to use the per-invocation workdir)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Natural-language task for qwen-code."},
                "repo_dir": {"type": "string", "description": "Absolute path to the repo. Defaults to the per-invocation workdir."},
                "model": {"type": "string", "description": "Model id served by the endpoint, e.g. 'qwen3.6-27b'. Defaults to FORGEOS_QWEN_MODEL env."},
                "base_url": {"type": "string", "description": "OpenAI-compatible base URL. Defaults to FORGEOS_QWEN_BASE_URL env."},
                "timeout": {"type": "integer", "default": 1200, "minimum": 60, "maximum": 3600},
            },
            "required": ["task"],
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


def _per_invocation_workdir(agent_context: dict | None) -> str:
    """Return the per-invocation default workdir for forgeos-lens-builder.

    Two concurrent runs must not share `/tmp/forgeos-lens-builder/forgeos-lens`
    or they'll trample each other's git state. We derive a unique path from
    the invocation_id that `executor.invoke()` puts in agent_context.

    Falls back to the legacy shared path when no invocation_id is present
    (e.g. running outside the platform), so local debugging still works.
    """
    inv = (agent_context or {}).get("invocation_id") if agent_context else None
    if inv:
        # Keep the segment short and filesystem-safe.
        slug = "".join(c for c in str(inv) if c.isalnum() or c in "-_")[:24]
        if slug:
            return f"/tmp/forgeos-lens-builder/run-{slug}/forgeos-lens"
    return "/tmp/forgeos-lens-builder/forgeos-lens"


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


def shell_exec(
    *,
    cmd: str,
    cwd: str | None = None,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    agent_context: dict | None = None,
) -> dict[str, Any]:
    # When the model omits cwd, route the call into a per-invocation workdir
    # so two concurrent runs can't trample each other's git state. We create
    # the directory on demand (mkdir -p) so the agent's first command can be
    # `git clone <repo> .` and land in the right place without knowing the
    # exact path.
    if not cwd:
        per_inv = _per_invocation_workdir(agent_context)
        try:
            Path(per_inv).mkdir(parents=True, exist_ok=True)
            cwd = per_inv
        except OSError:
            cwd = "/tmp"
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


def fs_write_file(
    *,
    path: str,
    content: str,
    cwd: str | None = None,
    append: bool = False,
    agent_context: dict | None = None,
) -> dict[str, Any]:
    """Write `content` to `path`. Relative paths resolve against `cwd` (or the
    per-invocation workdir). Creates parent directories."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        base = cwd or _per_invocation_workdir(agent_context)
        p = Path(base).expanduser() / p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(content)
        size = p.stat().st_size
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}", "path": str(p)}
    return {
        "ok": True,
        "path": str(p),
        "bytes_written": len(content.encode("utf-8")),
        "size": size,
        "append": append,
    }


def code_qwen_code_run(
    *,
    task: str,
    repo_dir: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: int = 1200,
    agent_context: dict | None = None,
) -> dict[str, Any]:
    """Drive `qwen` (https://github.com/QwenLM/qwen-code) non-interactively.

    Configuration is purely env-driven so we never need to write
    `~/.qwen/settings.json` on the platform container:
        OPENAI_API_KEY   — auth (use 'EMPTY' for unauth'd vLLM endpoints)
        OPENAI_BASE_URL  — vLLM/OpenAI-compatible endpoint
        OPENAI_MODEL     — model id served by the endpoint
    """
    if not repo_dir:
        repo_dir = _per_invocation_workdir(agent_context)
        try:
            Path(repo_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    cwd_or_err = _resolve_cwd(repo_dir)
    if isinstance(cwd_or_err, str):
        return {"ok": False, "error": cwd_or_err}
    model = model or os.environ.get("FORGEOS_QWEN_MODEL") or "qwen3.6-27b"
    base_url = base_url or os.environ.get("FORGEOS_QWEN_BASE_URL") or "http://79.117.23.16:20327/v1"
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("FORGEOS_QWEN_API_KEY") or "EMPTY"
    env = {
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": base_url,
        "OPENAI_MODEL": model,
        # qwen-code retries persistently on transient endpoint errors when this
        # is set — better fit for autonomous runs than the default fast-fail.
        "QWEN_CODE_UNATTENDED_RETRY": "1",
    }
    argv = [
        "qwen",
        "--prompt", task,
        "--output-format", "json",
        "--yolo",  # auto-approve actions — required for non-interactive runs
    ]
    result = _run(argv, cwd_or_err, timeout=timeout, env=env)
    diff = _run(["git", "status", "--porcelain"], cwd_or_err, timeout=20)
    files_changed: list[str] = []
    for line in (diff.get("stdout") or "").splitlines():
        if len(line) > 3:
            files_changed.append(line[3:].strip())
    result["files_changed"] = files_changed
    result["model"] = model
    result["base_url"] = base_url
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
    # produced". Without this escape hatch, qwen-code-driven workflows
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
    # Always `git add -A` so we include qwen-code's new files, deletions, and
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
    inp["agent_context"] = agent_context
    # subprocess.run inside an async handler would block the event loop —
    # under Cloud Run, that stalls the HTTP server, the scheduler, every
    # other in-flight invocation, and (when the call lasts minutes) gets
    # the instance flagged unresponsive. Offload to a worker thread.
    result = await asyncio.to_thread(shell_exec, **inp)
    return {"success": True, "result": result}


async def _handle_fs_write_file(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    inp = dict(tool_input)
    inp["agent_context"] = agent_context
    result = await asyncio.to_thread(fs_write_file, **inp)
    return {"success": True, "result": result}


async def _handle_qwen_code_run(tool_input: dict, agent_context: dict | None = None) -> dict[str, Any]:
    inp = dict(tool_input)
    inp["agent_context"] = agent_context
    result = await asyncio.to_thread(code_qwen_code_run, **inp)
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
        # Use HTTP Basic with the x-access-token convention, NOT
        # `Authorization: Bearer`. GitHub rejects Bearer for git operations
        # ("Password authentication is not supported"), especially for `gho_`
        # OAuth tokens. A one-shot credential helper feeds the token as the
        # password without writing it to repo config.
        env = {"GH_TOKEN": token, "GITHUB_TOKEN": token}
        helper = "!f() { echo username=x-access-token; echo \"password=$GH_TOKEN\"; }; f"
        retry = _run(
            ["git", "-c", f"credential.helper={helper}",
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
    "fs__write_file": _handle_fs_write_file,
    "code__qwen_code_run": _handle_qwen_code_run,
    "git__commit_push": _handle_git_commit_push,
    "gh__open_pr": _handle_gh_open_pr,
}
