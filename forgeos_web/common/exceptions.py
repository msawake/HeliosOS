"""DRF exception handler that reproduces FastAPI's error envelope.

FastAPI serves errors as ``{"detail": ...}`` (string for HTTPException, list of
field errors for validation). DRF's default emits varied shapes; this handler
normalizes every error to ``{"detail": ...}`` so the dashboard's error parsing
keeps working unchanged.
"""

from __future__ import annotations

from rest_framework.views import exception_handler as drf_default_handler


def forgeos_exception_handler(exc, context):
    response = drf_default_handler(exc, context)
    if response is None:
        return None  # unhandled -> Django 500

    data = response.data
    # Already the canonical shape.
    if isinstance(data, dict) and set(data.keys()) == {"detail"}:
        return response

    # DRF puts a top-level "detail" on most APIException subclasses already;
    # otherwise wrap the validation/error payload under "detail".
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        return response
    response.data = {"detail": data}
    return response
