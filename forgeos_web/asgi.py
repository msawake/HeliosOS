"""ASGI entry point for the ForgeOS Django app (served by uvicorn).

Replaces ``PlatformBootstrap.run_api_server`` (bootstrap.py:1031). The platform
singletons are installed into ``di.AppContext`` during boot; this module only
exposes the ASGI callable.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgeos_web.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

application = get_asgi_application()

# In DEBUG, serve Django admin static (CSS/JS) directly under ASGI so the admin
# renders styled without a separate static server. In production use whitenoise
# or a CDN + collectstatic instead.
from django.conf import settings  # noqa: E402

if settings.DEBUG:
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402

    application = ASGIStaticFilesHandler(application)
