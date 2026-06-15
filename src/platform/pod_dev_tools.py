"""Run the dev tools (`shell__exec`, `fs__write_file`, `git__commit_push`,
`gh__open_pr`) *inside* an agent's attached environment pod instead of on the
platform host.

When an agent has an attached, running environment (`EnvironmentManager.binding`
returns a `running` pod), `ToolExecutor` redirects these four tools here. Each
handler maps the tool to a `kubectl exec` command and returns the **same result
shape** as the local `dev_tools` handler, so the agentic loop and the audit-tail
logic in `tool_executor.execute` are unaffected. Admission already happened
(`tool.call`); this is a pure execution-backend swap — we do NOT re-fire the
`env.exec` syscall (that would double-reserve the budget).

The GH token rides in as a per-exec env var (never in the pod spec / host env),
mirroring `dev_tools`' inline credential-helper approach.
"""

from __future__ import annotations

import base64
import logging
import shlex
from typing import Any

from src.platform.dev_tools import SHELL_ALLOWLIST, _gh_token_from_ctx

logger = logging.getLogger(__name__)

POD_ROUTABLE_TOOLS = {"shell__exec", "fs__write_file", "git__commit_push", "gh__open_pr"}

# One-shot git credential helper: feeds the token as the password via the
# x-access-token convention (GitHub rejects Bearer for git push). Never written
# to repo config. Mirrors dev_tools._git_commit_push_with_token.
_GIT_CRED_HELPER = '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'


def _pod_workdir(agent_context: dict | None) -> str:
    """Per-invocation workdir inside the pod (mirrors dev_tools' host scheme so
    behaviour is identical whether a tool runs locally or in the pod)."""
    inv = (agent_context or {}).get("invocation_id") if agent_context else None
    if inv:
        slug = "".join(c for c in str(inv) if c.isalnum() or c in "-_")[:24]
        if slug:
            return f"/workspace/run-{slug}"
    return "/workspace"


def _resolve_pod_cwd(cwd: str | None, agent_context: dict | None) -> str:
    if not cwd or cwd.strip() in (".", "./"):
        return _pod_workdir(agent_context)
    return cwd


async def _exec(env_mgr: Any, agent_id: str, command: str, *,
                stdin: str | None = None, env: dict[str, str] | None = None,
                timeout: int = 300) -> dict[str, Any]:
    return await env_mgr.exec(agent_id, command, stdin=stdin, env=env, timeout=timeout)


def _coerce_timeout(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, AttributeError):
            return default
    return default


# --- per-tool handlers ------------------------------------------------------

async def _pod_shell_exec(env_mgr, agent_id, tool_input, ctx) -> dict[str, Any]:
    cmd = (tool_input or {}).get("cmd") or ""
    if not cmd.strip():
        return {"ok": False, "error": "empty cmd"}
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return {"ok": False, "error": f"could not parse cmd: {e}"}
    if not argv:
        return {"ok": False, "error": "empty cmd"}
    bin_name = argv[0].rsplit("/", 1)[-1]
    if bin_name not in SHELL_ALLOWLIST:
        return {"ok": False, "error": f"binary '{bin_name}' not in allowlist {sorted(SHELL_ALLOWLIST)}"}
    cwd = _resolve_pod_cwd(tool_input.get("cwd"), ctx)
    timeout = _coerce_timeout(tool_input.get("timeout"), 300)
    env = {}
    if isinstance(tool_input.get("env"), dict):
        env.update({str(k): str(v) for k, v in tool_input["env"].items()})
    token = _gh_token_from_ctx(ctx)
    if token:
        env.setdefault("GH_TOKEN", token)
        env.setdefault("GITHUB_TOKEN", token)
    command = f"mkdir -p {shlex.quote(cwd)} && cd {shlex.quote(cwd)} && {cmd}"
    out = await _exec(env_mgr, agent_id, command, env=env or None, timeout=timeout)
    return {"ok": out["ok"], "stdout": out["stdout"], "stderr": out["stderr"], "returncode": out["code"]}


async def _pod_fs_write_file(env_mgr, agent_id, tool_input, ctx) -> dict[str, Any]:
    path = (tool_input or {}).get("path") or ""
    content = (tool_input or {}).get("content") or ""
    append = bool((tool_input or {}).get("append"))
    if not path:
        return {"ok": False, "error": "path is required"}
    # Relative paths resolve against cwd or the per-invocation workdir.
    if not path.startswith("/"):
        base = tool_input.get("cwd") or _pod_workdir(ctx)
        path = f"{base.rstrip('/')}/{path}"
    redirect = ">>" if append else ">"
    # base64 over stdin: no quoting/heredoc hazard for arbitrary content.
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    command = (
        f"mkdir -p \"$(dirname {shlex.quote(path)})\" && "
        f"base64 -d {redirect} {shlex.quote(path)}"
    )
    out = await _exec(env_mgr, agent_id, command, stdin=b64, timeout=120)
    if not out["ok"]:
        return {"ok": False, "error": out["stderr"] or "write failed", "path": path}
    n = len(content.encode("utf-8"))
    return {"ok": True, "path": path, "bytes_written": n, "size": n, "append": append}


async def _pod_git_commit_push(env_mgr, agent_id, tool_input, ctx) -> dict[str, Any]:
    token = _gh_token_from_ctx(ctx)
    repo_dir = _resolve_pod_cwd((tool_input or {}).get("repo_dir"), ctx)
    branch = (tool_input or {}).get("branch") or ""
    message = (tool_input or {}).get("message") or ""
    if not branch or not message:
        return {"ok": False, "error": "branch and message are required"}
    cred = ""
    env = None
    if token:
        cred = f"-c credential.helper={shlex.quote(_GIT_CRED_HELPER)} "
        env = {"GH_TOKEN": token, "GITHUB_TOKEN": token}
    command = (
        f"cd {shlex.quote(repo_dir)} && "
        f"git checkout -B {shlex.quote(branch)} && "
        f"git add -A && "
        f"git commit -m {shlex.quote(message)} && "
        f"git {cred}push -u origin {shlex.quote(branch)}"
    )
    out = await _exec(env_mgr, agent_id, command, env=env, timeout=120)
    return {
        "ok": out["ok"], "branch": branch, "base": (tool_input or {}).get("base", "main"),
        "stdout": out["stdout"], "stderr": out["stderr"], "returncode": out["code"],
    }


async def _pod_gh_open_pr(env_mgr, agent_id, tool_input, ctx) -> dict[str, Any]:
    token = _gh_token_from_ctx(ctx)
    if not token:
        return {"ok": False, "error": "GH_TOKEN/GITHUB_TOKEN not set; cannot open PR"}
    repo_dir = _resolve_pod_cwd((tool_input or {}).get("repo_dir"), ctx)
    branch = (tool_input or {}).get("branch") or ""
    title = (tool_input or {}).get("title") or ""
    body = (tool_input or {}).get("body") or ""
    base = (tool_input or {}).get("base", "main")
    command = (
        f"cd {shlex.quote(repo_dir)} && "
        f"gh pr create --base {shlex.quote(base)} --head {shlex.quote(branch)} "
        f"--title {shlex.quote(title)} --body {shlex.quote(body)}"
    )
    out = await _exec(env_mgr, agent_id, command,
                      env={"GH_TOKEN": token, "GITHUB_TOKEN": token}, timeout=60)
    url = ""
    for line in (out.get("stdout") or "").splitlines():
        line = line.strip()
        if line.startswith("https://github.com/") and "/pull/" in line:
            url = line
    return {
        "ok": out["ok"], "pr_url": url,
        "stdout": out["stdout"], "stderr": out["stderr"], "returncode": out["code"],
    }


_POD_HANDLERS = {
    "shell__exec": _pod_shell_exec,
    "fs__write_file": _pod_fs_write_file,
    "git__commit_push": _pod_git_commit_push,
    "gh__open_pr": _pod_gh_open_pr,
}


async def run_in_pod(env_mgr: Any, agent_id: str, tool_name: str,
                     tool_input: dict, agent_context: dict | None) -> dict[str, Any]:
    """Dispatch a pod-routable dev tool into the agent's environment pod.

    Returns the `{"success": True, "result": {...}}` envelope the local dev-tool
    handlers return so the caller (`ToolExecutor`) is unaffected."""
    handler = _POD_HANDLERS.get(tool_name)
    if handler is None:  # defensive — caller already gates on POD_ROUTABLE_TOOLS
        return {"success": False, "error": f"{tool_name} is not pod-routable"}
    try:
        result = await handler(env_mgr, agent_id, tool_input or {}, agent_context or {})
    except Exception as e:  # noqa: BLE001
        logger.exception("pod dev-tool %s failed", tool_name)
        return {"success": False, "error": f"{tool_name} failed in pod: {e}"}
    return {"success": True, "result": result}
