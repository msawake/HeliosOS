"""Django settings for the ForgeOS web layer (rewrite of the FastAPI app).

Config surface intentionally mirrors the existing platform env vars so the same
.env / Secret Manager wiring keeps working:
  DATABASE_URL | CLOUD_SQL_INSTANCE + DB_PRIVATE_IP   -> database
  REDIS_URL                                            -> Celery broker + caches
  FORGEOS_CORS_ORIGINS                                 -> CORS
  FORGEOS_AUTH_DISABLED / FORGEOS_SESSION_SECRET ...   -> auth (Workstream A)
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = the directory that holds the forgeos_web/ package.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the repo root so `manage.py` (and any Django entrypoint)
# resolves DATABASE_URL / REDIS_URL / etc. the same way src.bootstrap and
# forgeos_web.celery_app do. Without this, DATABASE_URL is unset and the DB
# silently falls back to in-memory SQLite (no auth_user, etc.).
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:  # pragma: no cover - dotenv optional; env may already be set
    pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # repo root


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or os.environ.get(
    "FORGEOS_SESSION_SECRET", "insecure-dev-only-change-me"
)
DEBUG = _flag("DJANGO_DEBUG", "0")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Behind Cloud Run (and any TLS-terminating proxy): Django sees the request as
# plain HTTP and would refuse CSRF-protected POSTs from an https:// page
# because the Origin header doesn't match the request scheme it observes.
# Cloud Run reliably sets X-Forwarded-Proto, so trust it for scheme detection.
# Harmless locally — the header isn't present when hitting :5000 directly.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Origins whose POSTs Django will accept CSRF tokens from. Defaults to all
# *.run.app and *.a.run.app (the Cloud Run-hosted dashboard + platform-api),
# plus localhost ports we hit in dev. Override with DJANGO_CSRF_TRUSTED_ORIGINS
# (comma-separated, full scheme://host[:port]). Wildcards on the subdomain
# are allowed; ports are NOT — provide the exact host:port for non-standard.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "https://*.run.app,https://*.a.run.app,http://localhost:3000,http://localhost:5000",
    ).split(",")
    if o.strip()
]

# Auth gate. The FastAPI app used FORGEOS_AUTH_DISABLED (default off in local dev).
FORGEOS_AUTH_ENABLED = not _flag("FORGEOS_AUTH_DISABLED", "0")

ROOT_URLCONF = "forgeos_web.urls"
ASGI_APPLICATION = "forgeos_web.asgi.application"
WSGI_APPLICATION = None  # ASGI-only (uvicorn)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    "django_celery_beat",
    # ForgeOS web apps
    "forgeos_web.health",
    "forgeos_web.auth_app",
    "forgeos_web.rbac",
    "forgeos_web.tenancy",
    "forgeos_web.eventbus",
    "forgeos_web.clients",
    "forgeos_web.agents",
    "forgeos_web.hitl",
    "forgeos_web.ontology",
    "forgeos_web.usercreds",
    "forgeos_web.environments",
    "forgeos_web.runtime",
    "forgeos_web.governance",
    # View-only apps (endpoint ports)
    "forgeos_web.approvals",
    "forgeos_web.mcps",
    "forgeos_web.oauth",
    "forgeos_web.kernel",
    "forgeos_web.namespaces",
    "forgeos_web.credentials",
    "forgeos_web.admin_app",
    "forgeos_web.intelligence",
    "forgeos_web.billing",
    "forgeos_web.audit_events",
    "forgeos_web.sandbox",
    "forgeos_web.chat",
    "forgeos_web.rls_policies",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "forgeos_web.common.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # RLS tenant scoping. Binds the contextvar + a default tenant per request;
    # ForgeOSAuthentication re-binds to the authenticated principal's tenant
    # within the view transaction (the authoritative set_config under
    # ATOMIC_REQUESTS).
    "forgeos_web.db.middleware.RLSMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
            ],
        },
    },
]

# --------------------------------------------------------------------------- #
# Database — DATABASE_URL (dev) or Cloud SQL private IP (prod).
# RLS correctness depends on ATOMIC_REQUESTS (the set_config(local) tenant var
# must live for the whole request transaction). CONN_MAX_AGE=0 keeps us safe
# under pgbouncer transaction pooling / Cloud Run.
# --------------------------------------------------------------------------- #
def _database() -> dict:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        try:
            import dj_database_url
        except ImportError:  # scaffold tolerates missing optional dep
            cfg: dict = {"ENGINE": "django.db.backends.postgresql"}
        else:
            cfg = dj_database_url.parse(url)
        cfg.update(CONN_MAX_AGE=0, ATOMIC_REQUESTS=True)
        return {"default": cfg}

    instance = os.environ.get("CLOUD_SQL_INSTANCE", "").strip()
    common = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "forgeos"),
        "USER": os.environ.get("DB_USER", "forgeos"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "CONN_MAX_AGE": 0,
        "ATOMIC_REQUESTS": True,
        "OPTIONS": {},
    }
    if instance and os.environ.get("DB_PRIVATE_IP"):
        # Prefer native psycopg over the pg8000 connector for Django.
        common["HOST"] = os.environ["DB_PRIVATE_IP"]
        common["PORT"] = os.environ.get("DB_PORT", "5432")
        return {"default": common}
    # Fallback for `manage.py check` / unit tests with no DB configured.
    return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}


DATABASES = _database()
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Caches / Redis (rate limiter + moved session stores; shared with Celery)
# --------------------------------------------------------------------------- #
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --------------------------------------------------------------------------- #
# DRF — auth/permission classes land in Workstream A. Error shape matches
# FastAPI's {"detail": ...} via a custom exception handler.
# --------------------------------------------------------------------------- #
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "forgeos_web.authn.authentication.ForgeOSAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "forgeos_web.authn.permissions.IsAuthenticatedOrPublicPath",
    ],
    "EXCEPTION_HANDLER": "forgeos_web.common.exceptions.forgeos_exception_handler",
    "UNAUTHENTICATED_USER": None,
}

# --------------------------------------------------------------------------- #
# CORS — same default origins + headers as fastapi_app.py.
# --------------------------------------------------------------------------- #
_cors = os.environ.get("FORGEOS_CORS_ORIGINS", "").strip()
if _cors:
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()]
else:
    CORS_ALLOWED_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "content-type", "authorization", "x-api-key", "x-forgeos-user", "x-forgeos-caller",
]

# --------------------------------------------------------------------------- #
# Celery (Workstream C) — declared here so `manage.py` subcommands can read it.
# --------------------------------------------------------------------------- #
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = REDIS_URL or "cache+memory://"

STATIC_URL = "static/"
USE_TZ = True
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
LOGGING_CONFIG = None  # platform configures logging in bootstrap
