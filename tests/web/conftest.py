"""Pytest bootstrap for the Django web-layer tests.

Configures Django settings + setup() once. Runs without pytest-django; the tests
use DRF's APIRequestFactory and don't hit the DB (AuthManager works DB-less for
signed-token paths).
"""

from __future__ import annotations

import os

import pytest


def pytest_configure():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.forgeos_web.settings")
    os.environ.setdefault("FORGEOS_SESSION_SECRET", "test-secret-not-for-prod-only")
    import django

    django.setup()


@pytest.fixture
def auth_manager():
    from src.api.auth import AuthManager

    return AuthManager(db_client=None, tenant_id="acme")
