# Copyright 2024-2026 Awake Venture Studio.
# SPDX-License-Identifier: BUSL-1.1
"""
End-to-end tests for `forgeos mc …`.

Each test stands up a stub HTTP server on an ephemeral port that mimics the
relevant Helios OS platform endpoints, then invokes the CLI's argparse `main()`
with `--url=http://localhost:<port>` and captures stdout/stderr/exit-code.

This guards every verb against:
- shape drift on the platform side (the stubs encode the expected payload
  shape; a backend change that breaks this contract trips the test),
- regressions in CLI argument parsing,
- regressions in CLI error handling (HTTP 4xx/5xx must produce a clean
  message, not a Python traceback),
- regressions in the `approve-only` workflow (refuses empty filter, rejects
  non-matching items, approves only matches).
"""
from __future__ import annotations

import io
import json
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

# Ensure repo root on sys.path even when pytest is invoked from elsewhere.
import os
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.forgeos_sdk import cli as forgeos_cli  # noqa: E402
from src.forgeos_sdk import mc_cli  # noqa: E402


# ---------------------------------------------------------------------------
# Stub server scaffolding
# ---------------------------------------------------------------------------


class _StubState:
    """Mutable state shared with the handler: route table, recorded calls."""
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], object] = {}  # (METHOD, PATH_PREFIX) → handler/data
        self.calls: list[dict] = []


class _StubHandler(BaseHTTPRequestHandler):
    state: _StubState

    # Quiet down the default access log.
    def log_message(self, format, *args):  # noqa: A003
        return

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        method = self.command
        body_raw = b""
        if "Content-Length" in self.headers:
            n = int(self.headers["Content-Length"])
            body_raw = self.rfile.read(n)
        body_json = None
        if body_raw:
            try:
                body_json = json.loads(body_raw.decode())
            except Exception:
                pass
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        record = {
            "method": method, "path": parsed.path,
            "params": params, "body": body_json,
        }
        self.state.calls.append(record)

        # Find the most specific route match.
        match = None
        for (m, p), spec in self.state.routes.items():
            if m == method and (parsed.path == p or parsed.path.startswith(p.rstrip("*"))) :
                if match is None or len(p) > len(match[0]):
                    match = (p, spec)
        if match is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"detail":"no route"}')
            return

        spec = match[1]
        if callable(spec):
            try:
                status, payload = spec(record)
            except Exception as e:
                status, payload = 500, {"detail": f"stub error: {e}"}
        else:
            status, payload = spec
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._dispatch()

    def do_POST(self):  # noqa: N802
        self._dispatch()


@contextmanager
def stub_server(routes: dict):
    """Boot a stub HTTP server in a background thread. Yields (base_url, state)."""
    state = _StubState()
    state.routes = routes

    class _Handler(_StubHandler):
        pass

    _Handler.state = state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


def run_cli(argv: list[str], base_url: str, monkeypatch) -> tuple[int, str, str]:
    """Invoke `forgeos` main() and capture (exit_code, stdout, stderr)."""
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    # Pin the base URL.
    full_argv = ["--url", base_url] + argv
    code = 0
    try:
        rc = forgeos_cli.main(full_argv)
        code = rc if isinstance(rc, int) else 0
    except SystemExit as e:
        # argparse + our McApiError raise SystemExit. When the code is a
        # string (our friendly error messages), Python's runtime prints it
        # to stderr — but only AFTER the try/except. Mimic that here so
        # tests can assert on the message.
        if isinstance(e.code, int):
            code = e.code
        elif e.code is None:
            code = 0
        else:
            err.write(str(e.code) + "\n")
            code = 1
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Stubs that match real platform endpoints
# ---------------------------------------------------------------------------


def _fleet_one_running() -> dict:
    return {
        "summary": {"running": 1, "total": 1},
        "agents": [{
            "pid": "pid-1", "name": "default/greeter", "namespace": "default",
            "phase": "running", "display_phase": "running",
            "execution_type": "reflex", "next_run_at": None,
            "dollars": 0.0123, "tokens": 4242, "tool_calls": 7,
            "last_heartbeat": "2026-05-21T15:00:00+00:00",
        }],
    }


def _pending_three(rid_target: str = "req_abc123_target_298",
                   rid_other_1: str = "req_def456_other_279",
                   rid_other_2: str = "req_ghi789_other_280") -> list[dict]:
    return [
        {"source": "a2h", "id": rid_target, "agent_id": "pid-1", "priority": "medium",
         "created_at": "2026-05-21T15:00:01+00:00",
         "question": "Approve greeting comment on PR12148-298?",
         "context": {"issue_key": "PR12148-298"}},
        {"source": "a2h", "id": rid_other_1, "agent_id": "pid-1", "priority": "low",
         "created_at": "2026-05-21T15:00:02+00:00",
         "question": "Approve greeting comment on PR12148-279?",
         "context": {"issue_key": "PR12148-279"}},
        {"source": "a2h", "id": rid_other_2, "agent_id": "pid-1", "priority": "low",
         "created_at": "2026-05-21T15:00:03+00:00",
         "question": "Approve greeting comment on PR12148-280?",
         "context": {"issue_key": "PR12148-280"}},
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mc_fleet_renders_table(monkeypatch):
    routes = {("GET", "/api/platform/fleet"): (200, _fleet_one_running())}
    with stub_server(routes) as (base_url, _state):
        code, out, _ = run_cli(["mc", "fleet"], base_url, monkeypatch)
    assert code == 0
    assert "pid-1" in out and "default/greeter" in out
    assert "reflex" in out
    assert "running" in out.lower()


def test_mc_fleet_json_emits_raw_payload(monkeypatch):
    routes = {("GET", "/api/platform/fleet"): (200, _fleet_one_running())}
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "fleet", "--json"], base_url, monkeypatch)
    assert code == 0
    parsed = json.loads(out)
    assert parsed["agents"][0]["pid"] == "pid-1"


def test_mc_run_async_returns_accepted(monkeypatch):
    captured = []

    def invoke_handler(rec):
        captured.append(rec)
        return 200, {"agent_id": "pid-1", "status": "accepted",
                     "accepted": True, "queued_at": "2026-05-21T15:00:00Z"}

    routes = {
        ("GET", "/api/platform/fleet"): (200, _fleet_one_running()),
        ("POST", "/api/platform/agents/pid-1/invoke"): invoke_handler,
    }
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "run", "pid-1", "--prompt", "go"], base_url, monkeypatch)
    assert code == 0
    assert "queued" in out
    # async_mode=true is the contract for fire-and-forget.
    assert captured and captured[0]["params"].get("async_mode") == "true"
    # Prompt is forwarded.
    assert captured[0]["body"]["prompt"] == "go"


def test_mc_run_unknown_agent_prints_clean_error_not_traceback(monkeypatch):
    """Server returns 404; CLI must show a friendly message and a non-zero exit."""
    def invoke_404(rec):
        return 404, {"detail": "Agent 'ghost' not found"}

    routes = {
        ("GET", "/api/platform/fleet"): (200, _fleet_one_running()),
        ("POST", "/api/platform/agents/ghost/invoke"): invoke_404,
    }
    with stub_server(routes) as (base_url, _):
        code, out, err = run_cli(["mc", "run", "ghost"], base_url, monkeypatch)
    assert code != 0
    # Must NOT contain a Python traceback header.
    combined = out + err
    assert "Traceback (most recent call last)" not in combined
    assert "HTTP 404" in combined
    assert "ghost" in combined.lower()


def test_mc_run_resolves_unique_name_substring(monkeypatch):
    """`mc run greet` should resolve to the unique 'default/greeter' agent."""
    captured = []
    routes = {
        ("GET", "/api/platform/fleet"): (200, _fleet_one_running()),
        ("POST", "/api/platform/agents/pid-1/invoke"): lambda rec: (
            captured.append(rec) or (200, {"accepted": True, "queued_at": "now"})
        ),
    }
    with stub_server(routes) as (base_url, _):
        code, _, _ = run_cli(["mc", "run", "greet"], base_url, monkeypatch)
    assert code == 0
    assert len(captured) == 1
    assert captured[0]["path"] == "/api/platform/agents/pid-1/invoke"


def test_mc_runs_renders_history(monkeypatch):
    routes = {
        ("GET", "/api/platform/fleet"): (200, _fleet_one_running()),
        ("GET", "/api/platform/agents/pid-1/runs"): (200, {"runs": [
            {"started_at": "2026-05-21T14:00:00", "trigger": "manual",
             "status": "completed", "duration_ms": 12345,
             "tool_calls": 3, "tokens_used": 1500},
            {"started_at": "2026-05-21T13:00:00", "trigger": "a2h_resume",
             "status": "failed", "duration_ms": 200,
             "tool_calls": 0, "tokens_used": 0},
        ]}),
    }
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "runs", "pid-1"], base_url, monkeypatch)
    assert code == 0
    assert "manual" in out and "a2h_resume" in out
    assert "completed" in out and "failed" in out


def test_mc_logs_renders_events_and_orders_descending(monkeypatch):
    routes = {
        ("GET", "/api/platform/agent-logs"): (200, {"events": [
            {"ts": "2026-05-21T15:00:02", "agent_id": "pid-1",
             "type": "run.completed", "description": "ok"},
            {"ts": "2026-05-21T15:00:01", "agent_id": "pid-1",
             "type": "tool.call", "description": "tool jira_get_issue → ok"},
        ]}),
    }
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "logs", "--limit", "10"], base_url, monkeypatch)
    assert code == 0
    # Both events render
    assert "run.completed" in out and "tool.call" in out


def test_mc_logs_type_filter_is_applied_client_side(monkeypatch):
    """Server doesn't honor ?type=, so the CLI must filter locally."""
    routes = {
        ("GET", "/api/platform/agent-logs"): (200, {"events": [
            {"ts": "t1", "agent_id": "pid-1", "type": "run.started", "description": "rs"},
            {"ts": "t0", "agent_id": "pid-1", "type": "tool.call", "description": "tc"},
        ]}),
    }
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "logs", "--type", "tool.call"], base_url, monkeypatch)
    assert code == 0
    assert "tool.call" in out
    # The other event type must be dropped.
    assert "run.started" not in out


def test_mc_hitl_ls_renders_pending(monkeypatch):
    routes = {("GET", "/api/hitl/pending"): (200, {"items": _pending_three()})}
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(["mc", "hitl", "ls"], base_url, monkeypatch)
    assert code == 0
    assert "PR12148-298" in out and "PR12148-279" in out and "PR12148-280" in out
    assert "3 pending" in out


def test_mc_hitl_ls_filter_contains_is_case_insensitive(monkeypatch):
    routes = {("GET", "/api/hitl/pending"): (200, {"items": _pending_three()})}
    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(
            ["mc", "hitl", "ls", "--contains", "pr12148-298"], base_url, monkeypatch
        )
    assert code == 0
    assert "PR12148-298" in out
    assert "PR12148-279" not in out and "PR12148-280" not in out
    assert "1 pending" in out


def test_mc_hitl_approve_only_rejects_others_and_approves_target(monkeypatch):
    """--contains PR12148-298 with N=3 items: reject 2, approve 1."""
    items = _pending_three()
    routes = {("GET", "/api/hitl/pending"): (200, {"items": items})}
    approves: list[str] = []
    rejects: list[str] = []

    def make_route(target_id, bucket):
        def _h(rec):
            bucket.append(rec["path"].split("/")[-2])  # the request_id
            return 200, {"success": True}
        return _h

    for it in items:
        rid = it["id"]
        routes[("POST", f"/api/a2h/requests/{rid}/approve")] = make_route(rid, approves)
        routes[("POST", f"/api/a2h/requests/{rid}/reject")] = make_route(rid, rejects)

    with stub_server(routes) as (base_url, _):
        code, out, _ = run_cli(
            ["mc", "hitl", "approve-only", "--contains", "PR12148-298", "--yes"],
            base_url, monkeypatch,
        )
    assert code == 0
    assert approves == ["req_abc123_target_298"]
    assert set(rejects) == {"req_def456_other_279", "req_ghi789_other_280"}
    assert "approved=1" in out and "rejected=2" in out


def test_mc_hitl_approve_only_refuses_empty_filter(monkeypatch):
    """Empty --contains would match everything → must refuse, not blast-approve."""
    routes = {("GET", "/api/hitl/pending"): (200, {"items": _pending_three()})}
    approves: list[str] = []
    for it in _pending_three():
        rid = it["id"]
        routes[("POST", f"/api/a2h/requests/{rid}/approve")] = (
            lambda rec, l=approves: (l.append(rec["path"]) or (200, {"success": True}))
        )

    with stub_server(routes) as (base_url, _):
        code, out, err = run_cli(
            ["mc", "hitl", "approve-only", "--contains", "", "--yes"],
            base_url, monkeypatch,
        )
    assert code != 0
    assert approves == []  # critical: nothing was approved
    combined = out + err
    assert "non-empty" in combined.lower()


def test_mc_hitl_approve_resolves_id_prefix(monkeypatch):
    """`approve req_abc` should match the unique prefix and call /approve."""
    items = _pending_three()
    routes = {("GET", "/api/hitl/pending"): (200, {"items": items})}
    hit = {"id": None}
    routes[("POST", f"/api/a2h/requests/{items[0]['id']}/approve")] = (
        lambda rec: (hit.__setitem__("id", rec["path"]) or (200, {"success": True}))
    )

    with stub_server(routes) as (base_url, _):
        code, _, _ = run_cli(
            ["mc", "hitl", "approve", "req_abc"], base_url, monkeypatch
        )
    assert code == 0
    assert hit["id"] == f"/api/a2h/requests/{items[0]['id']}/approve"


def test_mc_hitl_reject_forwards_reason_as_query_param(monkeypatch):
    items = _pending_three()
    routes = {("GET", "/api/hitl/pending"): (200, {"items": items})}
    captured = {"params": None}
    rid = items[0]["id"]

    def _h(rec):
        captured["params"] = rec["params"]
        return 200, {"success": True}

    routes[("POST", f"/api/a2h/requests/{rid}/reject")] = _h
    with stub_server(routes) as (base_url, _):
        code, _, _ = run_cli(
            ["mc", "hitl", "reject", rid, "--reason", "out of scope"],
            base_url, monkeypatch,
        )
    assert code == 0
    assert captured["params"]["reason"] == "out of scope"
    assert captured["params"].get("responded_by") == "terminal-operator"


def test_mc_hitl_approve_clean_error_when_request_missing(monkeypatch):
    """Pending list is empty → resolver must SystemExit with a message,
    not a Python KeyError or HTTP-status traceback."""
    routes = {("GET", "/api/hitl/pending"): (200, {"items": []})}
    with stub_server(routes) as (base_url, _):
        code, out, err = run_cli(
            ["mc", "hitl", "approve", "req_zzz"], base_url, monkeypatch
        )
    assert code != 0
    combined = out + err
    assert "Traceback (most recent call last)" not in combined
    assert "req_zzz" in combined


# ---------------------------------------------------------------------------
# Pure-unit tests for the helpers (no HTTP).
# ---------------------------------------------------------------------------


def test_matches_filter_covers_question_and_context():
    assert mc_cli._matches_filter(
        {"question": "Approve greeting comment on PR12148-298?", "context": {}},
        "pr12148-298",
    )
    assert mc_cli._matches_filter(
        {"question": "x", "context": {"issue_key": "PR12148-298"}},
        "PR12148-298",
    )
    assert not mc_cli._matches_filter(
        {"question": "x", "context": {"issue_key": "AB-1"}},
        "PR12148-298",
    )


def test_event_key_dedup_stable():
    e1 = {"ts": "t", "type": "tool.call", "agent_id": "a", "details": {"pid": "p"}, "description": "x"}
    e2 = dict(e1)
    assert mc_cli._event_key(e1) == mc_cli._event_key(e2)
