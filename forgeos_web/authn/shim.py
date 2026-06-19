"""Adapt a Django request to the surface ``AuthManager.authenticate`` reads.

AuthManager (src/api/auth.py) was written against a Flask-shaped request:
``request.headers.get(...)`` (case-insensitive) and ``request.remote_addr`` for
per-IP rate limiting. Django's ``request.headers`` is already case-insensitive,
so the only gap is ``remote_addr``. This mirrors the FastAPI ``_AuthReqShim``
(fastapi_app.py:461-467).
"""

from __future__ import annotations


class DjangoAuthRequest:
    def __init__(self, request):
        self.headers = request.headers  # case-insensitive Mapping with .get()
        self.remote_addr = request.META.get("REMOTE_ADDR") or "unknown"
