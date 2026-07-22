# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Terminal Mission Control.

`forgeos mc <verb>` — list the fleet, run agents, tail observability,
manage HITL approvals without touching the React UI.

Talks to the platform on FORGEOS_API_URL (default http://localhost:5099,
matching `make mc-platform`). Pure stdlib + httpx; no curses/rich deps.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Iterable

import httpx


DEFAULT_URL = os.environ.get("FORGEOS_API_URL", "http://localhost:5099")
DEFAULT_API_KEY = os.environ.get("FORGEOS_API_KEY")


# ──────────────────────────── output helpers ────────────────────────────


C_OK = "\033[32m"
C_ERR = "\033[31m"
C_WARN = "\033[33m"
C_DIM = "\033[2m"
C_BOLD = "\033[1m"
C_RST = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{C_OK}✓{C_RST} {msg}")


def _err(msg: str) -> None:
    print(f"{C_ERR}✗{C_RST} {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"{C_WARN}!{C_RST} {msg}")


def _table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print(f"{C_DIM}(none){C_RST}")
        return
    cols = list(zip(headers, *rows))
    widths = [max(len(str(c)) for c in col) for col in cols]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(f"{C_BOLD}{fmt.format(*headers)}{C_RST}")
    print(f"{C_DIM}{fmt.format(*['─' * w for w in widths])}{C_RST}")
    for r in rows:
        print(fmt.format(*[str(c) for c in r]))


# ──────────────────────────── http helpers ────────────────────────────


def _client(base_url: str | None, api_key: str | None) -> httpx.Client:
    headers = {}
    key = api_key or DEFAULT_API_KEY
    if key:
        headers["X-API-Key"] = key
    return httpx.Client(base_url=base_url or DEFAULT_URL, headers=headers, timeout=30.0)


class McApiError(SystemExit):
    """Friendly CLI error for non-2xx HTTP responses."""


def _raise_for_status(r: httpx.Response, method: str, path: str) -> None:
    if r.is_success:
        return
    # Try to extract the server's `detail` for a clean message.
    detail = None
    try:
        detail = r.json().get("detail")
    except Exception:
        detail = r.text[:200] if r.text else None
    raise McApiError(f"{method} {path} → HTTP {r.status_code}: {detail or '(no body)'}")


def _get(base_url, api_key, path: str, params: dict | None = None) -> Any:
    with _client(base_url, api_key) as c:
        r = c.get(path, params=params or {})
        _raise_for_status(r, "GET", path)
        return r.json()


def _post(base_url, api_key, path: str, params: dict | None = None, body: Any = None) -> Any:
    with _client(base_url, api_key) as c:
        r = c.post(path, params=params or {}, json=body)
        _raise_for_status(r, "POST", path)
        if not r.content:
            return {}
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"raw": r.text}


# ──────────────────────────── verbs ────────────────────────────


def cmd_fleet(args) -> int:
    data = _get(args.url, args.api_key, "/api/platform/fleet")
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    agents = data.get("agents", [])
    rows = [
        [
            a.get("pid", ""),
            a.get("name", ""),
            a.get("execution_type", "") or "-",
            a.get("display_phase") or a.get("phase", ""),
            a.get("next_run_at") or "-",
            f"{a.get('tool_calls', 0)}",
            f"${a.get('dollars', 0):.4f}",
        ]
        for a in agents
    ]
    _table(rows, ["PID", "NAME", "EXEC", "PHASE", "NEXT_RUN", "TOOLS", "COST"])
    summary = data.get("summary", {})
    _print_summary = ", ".join(f"{k}={v}" for k, v in summary.items() if v)
    if _print_summary:
        print(f"\n{C_DIM}{_print_summary}{C_RST}")
    return 0


def _resolve_agent_id(args) -> str:
    """Accept either a full pid or a unique name substring."""
    aid = args.agent_id
    # Quick path: if it looks like a pid (contains a dash and 4+ chars), try as-is
    try:
        data = _get(args.url, args.api_key, "/api/platform/fleet")
    except httpx.HTTPError:
        return aid
    agents = data.get("agents", [])
    exact = [a for a in agents if a.get("pid") == aid or a.get("name") == aid]
    if exact:
        return exact[0].get("pid", aid)
    matches = [a for a in agents if aid.lower() in (a.get("name") or "").lower()]
    if len(matches) == 1:
        pid = matches[0].get("pid", aid)
        if pid != aid:
            _warn(f"resolved '{aid}' → {matches[0].get('name')} (pid={pid})")
        return pid
    if len(matches) > 1:
        names = ", ".join(m.get("name", "?") for m in matches)
        raise SystemExit(f"ambiguous agent '{aid}': matches {names}")
    return aid  # let server 404


def cmd_run(args) -> int:
    pid = _resolve_agent_id(args)
    body = {"prompt": args.prompt or "", "context": {}}
    params = {"async_mode": "true"} if not args.sync else {}
    data = _post(args.url, args.api_key, f"/api/platform/agents/{pid}/invoke", params=params, body=body)
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    if data.get("accepted") or data.get("status") == "accepted":
        _ok(f"queued: pid={data.get('agent_id', pid)} at {data.get('queued_at', '?')}")
    else:
        _ok(f"completed: status={data.get('status')} duration={data.get('duration', 0):.2f}s "
            f"tools={data.get('tool_calls', 0)} tokens={data.get('tokens_used', 0)}")
        result = data.get("result")
        if result:
            print(f"\n{C_DIM}--- result ---{C_RST}\n{result}")
        if data.get("error"):
            _err(data["error"])
    return 0


def cmd_runs(args) -> int:
    pid = _resolve_agent_id(args)
    data = _get(args.url, args.api_key, f"/api/platform/agents/{pid}/runs", params={"page_size": args.limit})
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    rows = []
    for r in data.get("items", []):
        rows.append([
            (r.get("started_at") or "")[:19].replace("T", " "),
            r.get("trigger", "?"),
            r.get("status", "?"),
            f"{(r.get('duration_ms') or 0)/1000:.1f}s",
            f"{r.get('tool_calls', 0)}",
            f"{r.get('tokens_used', 0)}",
        ])
    _table(rows, ["STARTED", "TRIGGER", "STATUS", "DUR", "TOOLS", "TOKENS"])
    return 0


def _fmt_log_event(ev: dict) -> str:
    ts = (ev.get("ts") or "")[:19].replace("T", " ")
    typ = ev.get("type", "?")
    color = {
        "run.started": C_DIM,
        "run.completed": C_OK,
        "run.failed": C_ERR,
        "tool.call": "",
        "hitl.requested": C_WARN,
        "hitl.resolved": C_OK,
    }.get(typ, "")
    agent = ev.get("agent_id") or "-"
    desc = ev.get("description") or ""
    return f"{C_DIM}{ts}{C_RST}  {color}{typ:14}{C_RST}  {agent[:32]:32}  {desc}"


def _event_key(ev: dict) -> tuple:
    return (
        ev.get("ts", ""),
        ev.get("type", ""),
        ev.get("agent_id", ""),
        (ev.get("details") or {}).get("pid", ""),
        ev.get("description", ""),
    )


def cmd_logs(args) -> int:
    params: dict[str, Any] = {"limit": args.limit}
    if args.agent:
        pid = _resolve_agent_id(argparse.Namespace(agent_id=args.agent, url=args.url, api_key=args.api_key))
        # backend accepts either pid or name as ?agent_id=; use pid
        params["agent_id"] = pid

    def _filter(events: list) -> list:
        # Server doesn't honor a ?type= filter — apply it client-side so the
        # advertised flag actually works.
        if args.type:
            events = [e for e in events if (e.get("type") or "").startswith(args.type)]
        return events

    if not args.follow:
        data = _get(args.url, args.api_key, "/api/platform/agent-logs", params=params)
        events = _filter(data.get("events", []))
        if args.json:
            print(json.dumps(events, indent=2, default=str))
            return 0
        for ev in events:
            print(_fmt_log_event(ev))
        return 0

    seen: set = set()
    print(f"{C_DIM}— following logs (Ctrl-C to stop) —{C_RST}")
    try:
        while True:
            try:
                data = _get(args.url, args.api_key, "/api/platform/agent-logs", params=params)
            except httpx.HTTPError as e:
                _err(f"logs poll: {e}")
                time.sleep(2)
                continue
            for ev in _filter(data.get("events", [])):
                k = _event_key(ev)
                if k in seen:
                    continue
                seen.add(k)
                print(_fmt_log_event(ev))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{C_DIM}— stopped —{C_RST}")
        return 0


# ──────────────────────────── hitl verbs ────────────────────────────


def _list_pending(args) -> list[dict]:
    data = _get(args.url, args.api_key, "/api/hitl/pending")
    return data.get("items", []) if isinstance(data, dict) else (data or [])


def _matches_filter(item: dict, needle: str) -> bool:
    if not needle:
        return True
    n = needle.lower()
    hay = " ".join(
        str(x)
        for x in [
            item.get("question") or "",
            (item.get("context") or {}).get("issue_key") or "",
            (item.get("context") or {}).get("summary") or "",
            (item.get("context") or {}).get("description") or "",
        ]
    )
    return n in hay.lower()


def cmd_hitl_ls(args) -> int:
    items = _list_pending(args)
    if args.agent:
        items = [i for i in items if (i.get("agent_id") or "").lower().find(args.agent.lower()) >= 0]
    if args.contains:
        items = [i for i in items if _matches_filter(i, args.contains)]
    if args.json:
        print(json.dumps(items, indent=2, default=str))
        return 0
    rows = []
    for i in items:
        ctx = i.get("context") or {}
        issue = ctx.get("issue_key") or "-"
        q = (i.get("question") or "").replace("\n", " ")
        if len(q) > 72:
            q = q[:69] + "..."
        rows.append([
            (i.get("id") or "")[:14],
            i.get("source", "?"),
            (i.get("agent_id") or "")[:24],
            i.get("priority", "?"),
            issue,
            q,
        ])
    _table(rows, ["ID", "SRC", "AGENT", "PRIO", "ISSUE", "QUESTION"])
    print(f"\n{C_DIM}{len(items)} pending{C_RST}")
    return 0


def _resolve_request_id(args, needle: str) -> dict:
    items = _list_pending(args)
    exact = [i for i in items if i.get("id") == needle]
    if exact:
        return exact[0]
    prefix = [i for i in items if (i.get("id") or "").startswith(needle)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise SystemExit(f"ambiguous id prefix '{needle}': {len(prefix)} matches")
    raise SystemExit(f"no pending HITL request matches '{needle}'")


def cmd_hitl_approve(args) -> int:
    item = _resolve_request_id(args, args.id)
    rid = item["id"]
    data = _post(args.url, args.api_key, f"/api/a2h/requests/{rid}/approve",
                 params={"responded_by": args.responded_by})
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    _ok(f"approved {rid} (issue={(item.get('context') or {}).get('issue_key', '-')})")
    return 0


def cmd_hitl_reject(args) -> int:
    item = _resolve_request_id(args, args.id)
    rid = item["id"]
    params = {"responded_by": args.responded_by}
    if args.reason:
        params["reason"] = args.reason
    data = _post(args.url, args.api_key, f"/api/a2h/requests/{rid}/reject", params=params)
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    _ok(f"rejected {rid} (issue={(item.get('context') or {}).get('issue_key', '-')})")
    return 0


def cmd_hitl_approve_only(args) -> int:
    if not args.contains or not args.contains.strip():
        _err("--contains must be a non-empty filter (refusing to approve everything)")
        return 2
    items = _list_pending(args)
    snapshot = list(items)
    targets = [i for i in snapshot if _matches_filter(i, args.contains)]
    others = [i for i in snapshot if not _matches_filter(i, args.contains)]

    if not targets:
        _err(f"no pending HITL request matches '{args.contains}'")
        return 1
    if len(targets) > 1:
        _warn(f"{len(targets)} pending items match '{args.contains}' — will approve all")

    print(f"{C_BOLD}plan:{C_RST}")
    print(f"  approve  ({len(targets)}):")
    for t in targets:
        print(f"    {t['id'][:14]}  {(t.get('context') or {}).get('issue_key', '-')}")
    print(f"  reject   ({len(others)}):")
    for o in others:
        print(f"    {o['id'][:14]}  {(o.get('context') or {}).get('issue_key', '-')}")

    if not args.yes:
        ans = input("\nproceed? [y/N] ").strip().lower()
        if ans != "y":
            _warn("aborted")
            return 1

    reason = args.reject_reason or f"not matching filter '{args.contains}'"
    n_rejected = 0
    for o in others:
        try:
            _post(args.url, args.api_key, f"/api/a2h/requests/{o['id']}/reject",
                  params={"responded_by": args.responded_by, "reason": reason})
            n_rejected += 1
        except httpx.HTTPError as e:
            _err(f"reject {o['id']}: {e}")
    n_approved = 0
    for t in targets:
        try:
            _post(args.url, args.api_key, f"/api/a2h/requests/{t['id']}/approve",
                  params={"responded_by": args.responded_by})
            n_approved += 1
        except httpx.HTTPError as e:
            _err(f"approve {t['id']}: {e}")

    _ok(f"approved={n_approved}, rejected={n_rejected}")
    return 0


def cmd_watch(args) -> int:
    pid_args = argparse.Namespace(agent_id=args.agent_id, url=args.url, api_key=args.api_key)
    pid = _resolve_agent_id(pid_args)
    try:
        while True:
            print("\033[2J\033[H", end="")
            print(f"{C_BOLD}HELIOS OS MC — watching {pid}{C_RST}  "
                  f"{C_DIM}{time.strftime('%H:%M:%S')}{C_RST}\n")

            try:
                fleet = _get(args.url, args.api_key, "/api/platform/fleet")
                row = next((a for a in fleet.get("agents", []) if a.get("pid") == pid), None)
                if row:
                    print(f"{C_BOLD}fleet{C_RST}  phase={row.get('display_phase') or row.get('phase')}  "
                          f"exec={row.get('execution_type')}  tools={row.get('tool_calls')}  "
                          f"$={row.get('dollars'):.4f}\n")
            except httpx.HTTPError as e:
                _err(f"fleet: {e}")

            try:
                pending = _list_pending(args)
                pending_for_agent = [
                    p for p in pending
                    if pid in (p.get("agent_id") or "") or (row and row.get("name", "").endswith(p.get("agent_id") or ""))
                ]
                print(f"{C_BOLD}hitl pending ({len(pending_for_agent)}){C_RST}")
                if pending_for_agent:
                    for p in pending_for_agent[:5]:
                        ctx = p.get("context") or {}
                        print(f"  {p['id'][:14]}  {ctx.get('issue_key', '-'):<14}  {(p.get('question') or '')[:60]}")
                else:
                    print(f"  {C_DIM}(none){C_RST}")
                print()
            except httpx.HTTPError:
                pass

            try:
                logs = _get(args.url, args.api_key, "/api/platform/agent-logs",
                            params={"agent_id": pid, "limit": 10})
                events = logs.get("events", [])
                print(f"{C_BOLD}recent events ({len(events)}){C_RST}")
                for ev in events[-10:]:
                    print(" ", _fmt_log_event(ev))
            except httpx.HTTPError as e:
                _err(f"logs: {e}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


# ──────────────────────────── wiring ────────────────────────────


def register(sub: argparse._SubParsersAction) -> None:
    """Attach the `mc` subcommand group to the top-level argparse tree."""
    p_mc = sub.add_parser("mc", help="Terminal Mission Control")
    mcsub = p_mc.add_subparsers(dest="mc_cmd", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="emit raw JSON")

    p = mcsub.add_parser("fleet", help="List the agent fleet")
    _common(p); p.set_defaults(func=cmd_fleet)

    p = mcsub.add_parser("run", help="Invoke an agent (async by default)")
    p.add_argument("agent_id")
    p.add_argument("--prompt", default="", help="optional prompt (defaults to empty → server fallback)")
    p.add_argument("--sync", action="store_true", help="wait for completion")
    _common(p); p.set_defaults(func=cmd_run)

    p = mcsub.add_parser("runs", help="Recent run history for an agent")
    p.add_argument("agent_id")
    p.add_argument("--limit", type=int, default=20)
    _common(p); p.set_defaults(func=cmd_runs)

    p = mcsub.add_parser("logs", help="Tail platform agent-logs feed")
    p.add_argument("--agent", help="filter by agent id/name")
    p.add_argument("--type", help="filter by event type (run.started, tool.call, …)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--follow", "-f", action="store_true")
    p.add_argument("--interval", type=float, default=1.5)
    _common(p); p.set_defaults(func=cmd_logs)

    p_hitl = mcsub.add_parser("hitl", help="HITL inbox")
    hsub = p_hitl.add_subparsers(dest="hitl_cmd", required=True)

    p = hsub.add_parser("ls", help="List pending HITL requests")
    p.add_argument("--agent", help="filter by originating agent")
    p.add_argument("--contains", help="case-insensitive substring filter on question/context")
    _common(p); p.set_defaults(func=cmd_hitl_ls)

    p = hsub.add_parser("approve", help="Approve a pending request by id (or unique prefix)")
    p.add_argument("id")
    p.add_argument("--responded-by", default="terminal-operator")
    _common(p); p.set_defaults(func=cmd_hitl_approve)

    p = hsub.add_parser("reject", help="Reject a pending request by id (or unique prefix)")
    p.add_argument("id")
    p.add_argument("--reason", default="")
    p.add_argument("--responded-by", default="terminal-operator")
    _common(p); p.set_defaults(func=cmd_hitl_reject)

    p = hsub.add_parser("approve-only", help="Approve only items matching --contains; reject the rest")
    p.add_argument("--contains", required=True, help="filter to identify the items to APPROVE")
    p.add_argument("--reject-reason", default="")
    p.add_argument("--responded-by", default="terminal-operator")
    p.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    _common(p); p.set_defaults(func=cmd_hitl_approve_only)

    p = mcsub.add_parser("watch", help="Live dashboard for one agent")
    p.add_argument("agent_id")
    p.add_argument("--interval", type=float, default=2.0)
    _common(p); p.set_defaults(func=cmd_watch)
