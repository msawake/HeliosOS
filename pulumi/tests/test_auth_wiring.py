"""Pulumi mock unit test — auth provisioning wiring (no real infra).

Asserts the Secrets component creates the platform admin API key + dashboard
login password secrets (each with a version, so Cloud Run can reference
``:latest``). Uses ``pulumi.runtime.set_mocks`` — no GCP, no credentials.

Run from the pulumi/ dir:  cd pulumi && pytest tests
(requires the pulumi venv: pip install -r requirements.txt)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pulumi

# Make `components.*` importable when run from the repo root or pulumi/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Mocks(pulumi.runtime.Mocks):
    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        return [args.name + "_id", dict(args.inputs)]

    def call(self, args: pulumi.runtime.MockCallArgs):
        return {}


pulumi.runtime.set_mocks(_Mocks(), project="forgeos-gcp", stack="test", preview=False)

from components.secrets import Secrets  # noqa: E402


def _secrets():
    return Secrets(
        "forgeos",
        region="europe-west1",
        project="proj",
        database_url="postgresql://x",
        redis_url=None,
        config=pulumi.Config(),
    )


@pulumi.runtime.test
def test_admin_api_key_secret_created():
    s = _secrets()

    def check(secret_id):
        # The admin key Secret exists with the expected stable id.
        assert secret_id == "forgeos-admin-api-key", secret_id
        # …and it always has a version (random fallback), so Cloud Run's
        # secret_key_ref :latest resolves at deploy.
        assert "admin-api-key" in s.versions

    return s.admin_api_key.secret_id.apply(check)


@pulumi.runtime.test
def test_dashboard_password_secret_created():
    s = _secrets()

    def check(secret_id):
        assert secret_id == "forgeos-dashboard-password", secret_id
        assert "dashboard-password" in s.versions

    return s.dashboard_password.secret_id.apply(check)
