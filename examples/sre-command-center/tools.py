"""
Simulated SRE tools for the Command Center demo.

These simulate real infrastructure operations. In production, they'd call
kubectl, Datadog, PagerDuty, GitHub, etc. For the demo, they return
realistic data so the governance controls can be exercised.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone

_now = lambda: datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Alert / Monitoring
# ---------------------------------------------------------------------------

SIMULATED_ALERTS = [
    {"id": "ALT-001", "severity": "P0", "service": "auth-service",
     "message": "Database connection pool exhausted — 0 available connections",
     "source": "datadog", "triggered_at": _now()},
    {"id": "ALT-002", "severity": "P1", "service": "payment-gateway",
     "message": "Error rate >5% on /api/payments endpoint",
     "source": "pagerduty", "triggered_at": _now()},
    {"id": "ALT-003", "severity": "P3", "service": "search-index",
     "message": "Reindexing job running 2x longer than baseline",
     "source": "datadog", "triggered_at": _now()},
]

def query_alerts() -> dict:
    """Query active alerts from monitoring systems."""
    return {"alerts": SIMULATED_ALERTS, "count": len(SIMULATED_ALERTS)}


# ---------------------------------------------------------------------------
# Infrastructure (kubectl-like)
# ---------------------------------------------------------------------------

def kubectl_restart(deployment: str, namespace: str = "production") -> dict:
    """Restart a deployment (rolling restart). SAFE — no data loss."""
    return {"action": "restart", "deployment": deployment, "namespace": namespace,
            "status": "rolling_restart_initiated", "estimated_time": "60s", "timestamp": _now()}

def kubectl_scale(deployment: str, replicas: int, namespace: str = "production") -> dict:
    """Scale a deployment up or down."""
    return {"action": "scale", "deployment": deployment, "replicas": replicas,
            "namespace": namespace, "status": "scaling", "timestamp": _now()}

def kubectl_delete(resource: str, name: str, namespace: str = "production") -> dict:
    """DELETE a resource. DANGEROUS — this is in the denied list."""
    return {"action": "delete", "resource": resource, "name": name,
            "namespace": namespace, "status": "deleted", "timestamp": _now()}

def kubectl_exec(pod: str, command: str, namespace: str = "production") -> dict:
    """Exec into a running pod. DANGEROUS — this is in the denied list."""
    return {"action": "exec", "pod": pod, "command": command,
            "namespace": namespace, "output": "simulated output", "timestamp": _now()}

def toggle_feature_flag(flag: str, enabled: bool) -> dict:
    """Toggle a feature flag. SAFE."""
    return {"action": "toggle_feature_flag", "flag": flag, "enabled": enabled, "timestamp": _now()}

def rollback_deployment(deployment: str, revision: int = 0, namespace: str = "production") -> dict:
    """Rollback to previous revision. SAFE."""
    return {"action": "rollback", "deployment": deployment, "revision": revision or "previous",
            "namespace": namespace, "status": "rolling_back", "timestamp": _now()}

def drop_table(table: str, database: str = "production") -> dict:
    """DROP TABLE. EXTREMELY DANGEROUS — in the denied list."""
    return {"action": "drop_table", "table": table, "database": database, "timestamp": _now()}

def rm_rf(path: str) -> dict:
    """rm -rf. EXTREMELY DANGEROUS — in the denied list."""
    return {"action": "rm_rf", "path": path, "timestamp": _now()}


# ---------------------------------------------------------------------------
# Observability (logs, metrics, traces)
# ---------------------------------------------------------------------------

def query_logs(service: str, severity: str = "ERROR", limit: int = 20) -> dict:
    """Query structured logs from a service."""
    logs = [
        {"timestamp": _now(), "level": "ERROR", "service": service,
         "message": f"Connection pool exhausted: max_connections=100, active=100, waiting=47"},
        {"timestamp": _now(), "level": "ERROR", "service": service,
         "message": f"Timeout waiting for connection: 30000ms exceeded"},
        {"timestamp": _now(), "level": "WARN", "service": service,
         "message": f"Memory pressure: heap usage at 94%, GC frequency increased 3x"},
    ]
    return {"service": service, "severity": severity, "logs": logs[:limit], "count": len(logs)}

def query_metrics(service: str, metric: str = "error_rate") -> dict:
    """Query time-series metrics for a service."""
    return {"service": service, "metric": metric,
            "current": round(random.uniform(0.05, 0.15), 4),
            "baseline": 0.001, "threshold": 0.05,
            "status": "ABOVE_THRESHOLD", "timestamp": _now()}

def query_traces(service: str, trace_id: str = "") -> dict:
    """Query distributed traces for a service."""
    return {"service": service, "trace_id": trace_id or f"trace-{random.randint(1000,9999)}",
            "spans": 12, "duration_ms": 4500, "root_cause_span": "db-connection-pool",
            "bottleneck": "connection_acquire: 3800ms (84% of total)", "timestamp": _now()}


# ---------------------------------------------------------------------------
# Code Review / Deployment
# ---------------------------------------------------------------------------

def read_pr_diff(pr_number: int) -> dict:
    """Read a PR diff for code review."""
    return {"pr_number": pr_number, "title": "fix: increase connection pool size and add circuit breaker",
            "author": "engineer-1", "additions": 45, "deletions": 12,
            "files": ["src/db/pool.py", "src/middleware/circuit_breaker.py", "tests/test_pool.py"],
            "diff": "+POOL_SIZE = 200  # was 100\n+POOL_TIMEOUT = 60  # was 30\n+circuit_breaker = CircuitBreaker(threshold=5)\n-POOL_SIZE = 100\n-POOL_TIMEOUT = 30"}

def run_tests(suite: str = "all") -> dict:
    """Run test suite."""
    return {"suite": suite, "total": 342, "passed": 342, "failed": 0,
            "duration_seconds": 45, "coverage": "87%", "status": "PASS"}

def deploy_to_staging(service: str, version: str) -> dict:
    """Deploy a service to staging environment."""
    return {"service": service, "version": version, "environment": "staging",
            "status": "deployed", "health_check": "passing", "timestamp": _now()}

def deploy_to_production(service: str, version: str) -> dict:
    """Deploy a service to production. Requires HITL approval."""
    return {"service": service, "version": version, "environment": "production",
            "status": "deployed", "canary_traffic": "10%", "timestamp": _now()}

def check_active_incidents() -> dict:
    """Check if there are active P0/P1 incidents (for deploy policy)."""
    return {"active_p0": 1, "active_p1": 0, "deploy_allowed": False,
            "reason": "P0 incident ALT-001 is active — no deploys allowed"}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SAFE_TOOLS = {
    "platform__query_alerts": {"fn": query_alerts, "description": "Query active monitoring alerts"},
    "platform__query_logs": {"fn": query_logs, "description": "Query structured logs from a service"},
    "platform__query_metrics": {"fn": query_metrics, "description": "Query time-series metrics"},
    "platform__query_traces": {"fn": query_traces, "description": "Query distributed traces"},
    "platform__kubectl_restart": {"fn": kubectl_restart, "description": "Rolling restart a deployment (SAFE)"},
    "platform__kubectl_scale": {"fn": kubectl_scale, "description": "Scale a deployment (SAFE)"},
    "platform__toggle_feature_flag": {"fn": toggle_feature_flag, "description": "Toggle a feature flag (SAFE)"},
    "platform__rollback_deployment": {"fn": rollback_deployment, "description": "Rollback to previous version (SAFE)"},
    "platform__read_pr_diff": {"fn": read_pr_diff, "description": "Read PR diff for code review"},
    "platform__run_tests": {"fn": run_tests, "description": "Run test suite"},
    "platform__deploy_to_staging": {"fn": deploy_to_staging, "description": "Deploy to staging"},
    "platform__deploy_to_production": {"fn": deploy_to_production, "description": "Deploy to production (requires HITL)"},
    "platform__check_active_incidents": {"fn": check_active_incidents, "description": "Check for active incidents"},
}

DENIED_TOOLS = {
    "platform__kubectl_delete": {"fn": kubectl_delete, "description": "DELETE resources (DENIED by kernel)"},
    "platform__kubectl_exec": {"fn": kubectl_exec, "description": "Exec into pods (DENIED by kernel)"},
    "platform__drop_table": {"fn": drop_table, "description": "DROP TABLE (DENIED by kernel)"},
    "platform__rm_rf": {"fn": rm_rf, "description": "rm -rf (DENIED by kernel)"},
}

ALL_TOOLS = {**SAFE_TOOLS, **DENIED_TOOLS}
