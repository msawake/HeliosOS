"""Authentication & RBAC for the Django web layer.

Reuses the framework-agnostic crypto + AuthManager from src/api/auth.py (signed
session tokens, Firebase JWT, per-tenant + admin API keys, PBKDF2 passwords,
per-IP rate limiting) so existing tokens and stored hashes validate unchanged.
The Django-specific surface here is the DRF authentication/permission classes
and the tenant-binding glue.
"""
