"""Cookie-based password gate."""

import hashlib
import hmac
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import COOKIE_NAME, MC_PASSWORD

_LOGIN_HTML = (Path(__file__).parent / "templates" / "login.html").read_text()

router = APIRouter()


def _session_token() -> str:
    return hmac.new(MC_PASSWORD.encode("utf-8"), b"ok", hashlib.sha256).hexdigest()


def require_session(request: Request):
    if not MC_PASSWORD:
        return  # gate disabled (local dev convenience)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if not cookie or not hmac.compare_digest(cookie, _session_token()):
        raise HTTPException(status_code=302, headers={"Location": "/login"})


@router.get("/login", response_class=HTMLResponse)
async def login_form():
    return _LOGIN_HTML.replace("{ERROR}", "")


@router.post("/login")
async def login_submit(password: str = Form(...)):
    if not MC_PASSWORD or not hmac.compare_digest(password, MC_PASSWORD):
        return HTMLResponse(
            _LOGIN_HTML.replace("{ERROR}", '<p class="err">Invalid password</p>'),
            status_code=401,
        )
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        _session_token(),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=8 * 3600,
    )
    return resp
