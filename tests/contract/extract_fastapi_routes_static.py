"""Statically extract the FastAPI route inventory from fastapi_app.py.

A fallback for environments where the full platform can't be imported (to run
the live ``snapshot_fastapi.py``). Parses the AST for ``@app.<method>("path")``
decorators — no imports, no platform deps — and writes the same
``fastapi_routes.json`` schema the parity test consumes.

    PYTHONPATH=. python tests/contract/extract_fastapi_routes_static.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1] / "src" / "dashboard" / "fastapi_app.py"
METHODS = {"get", "post", "put", "patch", "delete", "websocket", "head", "options"}


def extract() -> list[dict]:
    tree = ast.parse(APP.read_text())
    routes: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            # match app.<method>(...) or <var>.<method>(...) where attr is an HTTP verb
            if not (isinstance(fn, ast.Attribute) and fn.attr in METHODS):
                continue
            if not dec.args:
                continue
            path_arg = dec.args[0]
            if not (isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str)):
                continue
            method = "GET" if fn.attr in ("websocket", "head", "options") else fn.attr.upper()
            if fn.attr == "websocket":
                method = "WEBSOCKET"
            routes.setdefault(path_arg.value, set()).add(method)
    return sorted(
        ({"path": p, "methods": sorted(m)} for p, m in routes.items()),
        key=lambda r: r["path"],
    )


def main() -> None:
    routes = extract()
    (HERE / "fastapi_routes.json").write_text(json.dumps(routes, indent=2))
    print(f"extracted {len(routes)} route paths -> tests/contract/fastapi_routes.json")
    methods = sorted({m for r in routes for m in r["methods"]})
    print("methods:", methods)


if __name__ == "__main__":
    main()
