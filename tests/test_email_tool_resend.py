"""Tests for the Resend-backed notify__email tool."""
import sys
import types

import pytest

from src.platform import email_tool as et


class _Resp:
    def __init__(self, status=200, json_data=None, content=b"{}"):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.text = ""

    def json(self):
        return self._json


@pytest.fixture
def fake(monkeypatch):
    calls = {}

    def post(url, headers=None, json=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        return _Resp(200, {"id": "re_msg_123"})

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))
    monkeypatch.setattr(et, "_resolve_secret",
                        lambda name: {"RESEND_API_KEY": "re_test", "RESEND_FROM": "Helios <no-reply@x.dev>"}.get(name, ""))
    return calls


def test_send_plaintext(fake):
    r = et.send_email(subject="Hi", body="hello", to="a@b.com")
    assert r["ok"] is True and r["message_id"] == "re_msg_123"
    assert fake["url"] == "https://api.resend.com/emails"
    assert fake["headers"]["Authorization"] == "Bearer re_test"
    assert fake["json"] == {"from": "Helios <no-reply@x.dev>", "to": ["a@b.com"], "subject": "Hi", "text": "hello"}


def test_send_html(fake):
    et.send_email(subject="S", body="<h1>x</h1>", to="a@b.com", html=True)
    assert "html" in fake["json"] and "text" not in fake["json"]


def test_missing_recipient(monkeypatch):
    monkeypatch.delenv("FORGEOS_AUDIT_EMAIL_TO", raising=False)
    r = et.send_email(subject="S", body="b")
    assert r["ok"] is False and "recipient" in r["error"]


def test_missing_api_key(monkeypatch):
    monkeypatch.setattr(et, "_resolve_secret", lambda name: "")
    r = et.send_email(subject="S", body="b", to="a@b.com")
    assert r["ok"] is False and "RESEND_API_KEY" in r["error"]


def test_missing_sender(monkeypatch):
    monkeypatch.setattr(et, "_resolve_secret", lambda name: "re_x" if name == "RESEND_API_KEY" else "")
    r = et.send_email(subject="S", body="b", to="a@b.com")
    assert r["ok"] is False and "RESEND_FROM" in r["error"]


def test_send_error_surfaced(monkeypatch):
    monkeypatch.setattr(et, "_resolve_secret",
                        lambda name: {"RESEND_API_KEY": "re", "RESEND_FROM": "f@x.dev"}.get(name, ""))
    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(post=lambda *a, **k: _Resp(422, content=b'{"e":1}')))
    r = et.send_email(subject="S", body="b", to="a@b.com")
    assert r["ok"] is False and "422" in r["error"]
