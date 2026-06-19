"""Security-header middleware — byte-identical to the FastAPI app.

Mirrors ``add_security_headers`` (fastapi_app.py:425-434). Custom (rather than
Django's SecurityMiddleware) so the exact header set, including X-XSS-Protection
and Referrer-Policy, is preserved for the dashboard contract.
"""

from __future__ import annotations


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["X-XSS-Protection"] = "1; mode=block"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.is_secure():
            response["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
