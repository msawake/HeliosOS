"""Tests for src/platform/gcp_provisioning.py — slug sanitize + provision/deprovision REST flow."""
import types

import pytest

from src.platform import gcp_provisioning as gp
from src.platform.gcp_provisioning import ProvisioningError, sanitize_account_id


# ---- slug sanitization -----------------------------------------------------

def test_sanitize_basic():
    assert sanitize_account_id("Invoice Reader") == "invoice-reader"


def test_sanitize_strips_and_collapses():
    assert sanitize_account_id("--Foo__Bar!!  Baz--") == "foo-bar-baz"


def test_sanitize_leading_nonalpha_gets_prefixed():
    out = sanitize_account_id("123abc")
    assert out.startswith("a-") and gp._ACCOUNT_ID_RE.match(out)


def test_sanitize_short_is_padded():
    out = sanitize_account_id("ab")
    assert len(out) >= 6 and gp._ACCOUNT_ID_RE.match(out)


def test_sanitize_long_is_clamped_valid():
    out = sanitize_account_id("x" * 80)
    assert 6 <= len(out) <= 30 and gp._ACCOUNT_ID_RE.match(out)


def test_sanitize_empty_falls_back():
    assert sanitize_account_id("") == "agent-sa"


# ---- provision / deprovision REST flow (mocked) ----------------------------

class _Resp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.ok = 200 <= status < 300

    def json(self):
        return self._json


@pytest.fixture
def fake_requests(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url, json))
        if url.endswith(":getIamPolicy"):
            return _Resp(200, {"bindings": [], "etag": "abc"})
        return _Resp(200, {})

    def delete(url, headers=None, timeout=None):
        calls.append(("DELETE", url, None))
        return _Resp(204)

    fake = types.SimpleNamespace(post=post, delete=delete, get=lambda *a, **k: _Resp(200, text=""))
    monkeypatch.setattr(gp, "_access_token", lambda: "tok")
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    return calls


def test_provision_creates_sa_and_grants(fake_requests):
    email = gp.provision_agent_sa(
        "invoice reader", project_id="ms-awake-dev",
        runtime_sa_email="rt@ms-awake-dev.iam.gserviceaccount.com", grant_bigquery=True,
    )
    assert email == "invoice-reader@ms-awake-dev.iam.gserviceaccount.com"
    urls = [c[1] for c in fake_requests]
    # SA create
    assert any(u.endswith("/projects/ms-awake-dev/serviceAccounts") for u in urls)
    # token-creator on the SA + BigQuery on the project (get+set each)
    assert sum(1 for u in urls if u.endswith(":setIamPolicy")) == 3  # 1 SA + 2 BQ roles
    assert any("cloudresourcemanager" in u for u in urls)


def test_provision_skips_bigquery_when_disabled(fake_requests):
    gp.provision_agent_sa("acme bot", project_id="p",
                          runtime_sa_email="rt@p.iam.gserviceaccount.com", grant_bigquery=False)
    urls = [c[1] for c in fake_requests]
    assert not any("cloudresourcemanager" in u for u in urls)
    assert sum(1 for u in urls if u.endswith(":setIamPolicy")) == 1  # only token-creator


def test_provision_requires_project():
    with pytest.raises(ProvisioningError):
        gp.provision_agent_sa("x", project_id="", runtime_sa_email="rt@p")


def test_deprovision_deletes(fake_requests):
    assert gp.deprovision_agent_sa(
        "invoice-reader@ms-awake-dev.iam.gserviceaccount.com", project_id="ms-awake-dev") is True
    assert fake_requests[-1][0] == "DELETE"


def test_deprovision_noop_without_email():
    assert gp.deprovision_agent_sa("", project_id="p") is False
