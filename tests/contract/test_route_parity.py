"""Route-parity regression net.

Diffs the live Django urlconf against the captured FastAPI contract
(`fastapi_routes.json`). Two guarantees:

1. NO DRIFT: every API path Django serves must exist in the FastAPI contract
   with the same HTTP methods (catches accidental path/method divergence in a
   port). This is a hard assertion.
2. COVERAGE: reports how many of the FastAPI routes are ported so far. Not-yet-
   ported paths do NOT fail the suite (the migration is incremental).

Run in the full env after installing the `django` extra:
    PYTHONPATH=. .venv-platform/bin/python -m pytest tests/contract/test_route_parity.py -v
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "fastapi_routes.json"

# Django converter syntax -> FastAPI brace syntax, e.g. <str:agent_id> -> {agent_id}
_CONVERTER = re.compile(r"<(?:[a-zA-Z_]+:)?([a-zA-Z_][a-zA-Z0-9_]*)>")
# FastAPI path-converter suffix inside braces, e.g. {name:path} -> {name}
_FASTAPI_CONV = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*):[a-zA-Z_]+\}")


def _normalize(path: str) -> str:
    path = _CONVERTER.sub(r"{\1}", path)
    path = _FASTAPI_CONV.sub(r"{\1}", path)
    # FastAPI paths have a leading slash and no trailing slash (except root).
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _django_routes() -> list[dict]:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgeos_web.settings")
    import django
    from django.urls import get_resolver
    from django.urls.resolvers import URLPattern, URLResolver

    django.setup()

    found: dict[str, set[str]] = {}

    def walk(patterns, prefix: str):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                full = _normalize(prefix + str(p.pattern))
                view = getattr(p.callback, "cls", None)
                methods: set[str] = set()
                if view is not None:  # class-based view (DRF APIView)
                    for m in ("get", "post", "put", "patch", "delete"):
                        if hasattr(view, m):
                            methods.add(m.upper())
                else:  # function view: read an explicit method tag if present
                    tagged = getattr(p.callback, "forgeos_methods", None)
                    if tagged:
                        methods.update(m.upper() for m in tagged)
                found.setdefault(full, set()).update(methods or {"GET"})

    walk(get_resolver().url_patterns, "")
    # Compare the ForgeOS surface; skip Django's own admin/static/internal mounts
    # (not part of the FastAPI contract).
    _SKIP = ("/admin", "/static", "/media", "/__debug__")
    return [
        {"path": k, "methods": sorted(v)}
        for k, v in found.items()
        if not any(k.startswith(p) for p in _SKIP)
    ]


@pytest.fixture(scope="module")
def contract() -> dict[str, set[str]]:
    if not SNAPSHOT.exists():
        pytest.skip("fastapi_routes.json snapshot missing — run snapshot_fastapi.py first")
    data = json.loads(SNAPSHOT.read_text())
    return {_normalize(r["path"]): set(r["methods"]) for r in data}


# Routes added in Django AFTER the FastAPI cutover (no FastAPI equivalent). The
# parity gate guards against accidental drift in the *port*; intentional new
# Django-native endpoints are recorded here so they don't trip the hard
# assertion. Keep `fastapi_routes.json` a pure FastAPI snapshot.
_DJANGO_NATIVE_ADDITIONS = {
    "/api/platform/namespaces/mine",
    "/api/platform/namespaces/{ns}/members",
    "/api/platform/namespaces/{ns}/members/{member_user_id}",
    # Run history: list runs across the fleet, grouped client-side by conversation.
    "/api/platform/runs",
}


def test_no_path_drift(contract):
    """Every Django API path must exist in the FastAPI contract (same methods),
    except explicitly-recorded Django-native additions."""
    drift: list[str] = []
    for route in _django_routes():
        path, methods = route["path"], set(route["methods"])
        if path in _DJANGO_NATIVE_ADDITIONS:
            continue
        if path not in contract:
            drift.append(f"{path} {sorted(methods)} — not in FastAPI contract")
            continue
        extra = methods - contract[path]
        if extra:
            drift.append(f"{path} — methods {sorted(extra)} not in contract {sorted(contract[path])}")
    assert not drift, "Django routes diverge from the FastAPI contract:\n" + "\n".join(drift)


def test_coverage_report(contract, capsys):
    """Informational: how much of the 151-route contract is ported."""
    django_paths = {r["path"] for r in _django_routes()}
    ported = django_paths & set(contract)
    pct = (len(ported) / len(contract) * 100) if contract else 0
    with capsys.disabled():
        print(f"\nRoute coverage: {len(ported)}/{len(contract)} FastAPI routes ported ({pct:.0f}%)")
