"""Lightweight server-side pagination helper for hand-built DRF APIViews.

Usage::

    from forgeos_web.common.pagination import paginate

    def get(self, request):
        items = build_full_list()
        return Response(paginate(items, request))

Response shape::

    {"items": [...], "total": N, "page": 1, "page_size": 20}

Query params: ``?page=1&page_size=20`` (page_size capped at 200).
"""
from __future__ import annotations


def paginate(items: list, request, default: int = 20) -> dict:
    """Slice *items* according to ``?page`` / ``?page_size`` and return a dict."""
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(200, int(request.query_params.get("page_size", default))))
    except (ValueError, TypeError):
        page_size = default
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def parse_page(request, default: int = 20) -> tuple[int, int]:
    """Return (page, page_size) parsed from query params."""
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = max(1, min(200, int(request.query_params.get("page_size", default))))
    except (ValueError, TypeError):
        page_size = default
    return page, page_size
