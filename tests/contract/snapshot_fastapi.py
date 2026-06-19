"""Capture the legacy FastAPI route + OpenAPI contract to a JSON snapshot.

Run ONCE (and re-run whenever the FastAPI app legitimately changes) in the full
platform env where fastapi + deps are installed:

    PYTHONPATH=. .venv-platform/bin/python tests/contract/snapshot_fastapi.py

Writes:
    tests/contract/fastapi_routes.json   # [{"path","methods"}], the 151-route inventory
    tests/contract/fastapi_openapi.json  # full OpenAPI doc (request/response schemas)

The route factory is called with all dependencies as None and auth disabled —
routes register at factory-call time without touching the injected objects, so
this captures the path surface without a DB/Redis/LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build_app():
    repo_root = HERE.parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.dashboard.fastapi_app import create_fastapi_app

    # auth disabled + all deps None: register routes without live subsystems.
    return create_fastapi_app(auth_enabled=False)


def route_inventory(app) -> list[dict]:
    out: list[dict] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path:
            continue
        out.append({
            "path": path,
            "methods": sorted(m for m in (methods or []) if m not in ("HEAD", "OPTIONS")),
        })
    out.sort(key=lambda r: (r["path"], ",".join(r["methods"])))
    return out


def main() -> None:
    app = build_app()
    routes = route_inventory(app)
    (HERE / "fastapi_routes.json").write_text(json.dumps(routes, indent=2))
    try:
        openapi = app.openapi()
        (HERE / "fastapi_openapi.json").write_text(json.dumps(openapi, indent=2, sort_keys=True))
    except Exception as e:  # openapi generation can need more wiring; routes are the key artifact
        print(f"warning: could not generate openapi.json: {e}")
    print(f"captured {len(routes)} routes -> tests/contract/fastapi_routes.json")


if __name__ == "__main__":
    main()
