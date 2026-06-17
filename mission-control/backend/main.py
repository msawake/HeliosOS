"""Helios OS Mission Control — FastAPI entrypoint.

Serves:
  - /login          → password gate
  - /api/*          → proxied to FORGEOS_API_URL with Bearer token
  - /  (and SPA)    → Vite-built React static files from ./static
"""

from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .auth import require_session, router as auth_router
from .config import FORGEOS_API, PORT
from .proxy import router as proxy_router

# docs_url=None disables FastAPI's built-in Swagger UI at /docs so we can serve
# the mkdocs-built documentation site at that path instead.
app = FastAPI(title="Helios OS Mission Control", docs_url=None, redoc_url=None)
app.include_router(auth_router)
app.include_router(proxy_router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", dependencies=[Depends(require_session)])
async def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        raise HTTPException(status_code=503, detail="Frontend build missing")
    return FileResponse(idx)


@app.get("/{path:path}", dependencies=[Depends(require_session)])
async def spa(path: str, request: Request):
    # Static asset (js/css/img). Anything else falls back to index.html for SPA routing.
    target = (STATIC_DIR / path).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404)
    if target.is_file():
        return FileResponse(target)
    # mkdocs publishes pages as directories (e.g. /docs/guides/defining-agents/).
    # Resolve a trailing directory request to its index.html. If the request URL
    # has no trailing slash, redirect first — otherwise the browser resolves the
    # mkdocs site's relative asset URLs (assets/stylesheets/main.css, ...) against
    # the parent path and 404s on every stylesheet/script.
    if target.is_dir():
        dir_idx = target / "index.html"
        if dir_idx.is_file():
            if not request.url.path.endswith("/"):
                return RedirectResponse(url=request.url.path + "/", status_code=308)
            return FileResponse(dir_idx)
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        raise HTTPException(status_code=404)
    return FileResponse(idx)


if __name__ == "__main__":
    print(f"Helios OS Mission Control -> {FORGEOS_API}")
    print(f"Open http://localhost:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
