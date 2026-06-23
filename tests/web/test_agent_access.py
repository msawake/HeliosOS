"""Ownership-based agent access control — _can_access_agent matrix.

Pure-function tests (no DB): membership is supplied via my_namespaces, so this
exercises the personal/shared/client + role decision table directly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.kernel


def _agent(ownership: str, *, owner_id=None, namespace="default"):
    return type(
        "A", (),
        {"ownership": type("O", (), {"value": ownership})(), "owner_id": owner_id, "namespace": namespace},
    )()


def _can(uid, role, agent_def, my_ns):
    from forgeos_web.agents.views import _can_access_agent

    return _can_access_agent(uid, role, agent_def, my_namespaces=my_ns)


class TestCanAccessAgent:
    def test_tenant_admin_sees_everything(self):
        assert _can("anyone", "admin", _agent("personal", owner_id="alice"), set()) is True
        assert _can("anyone", "admin", _agent("shared", namespace="sales"), set()) is True
        assert _can("anyone", "admin", _agent("client", owner_id="acme"), set()) is True

    def test_personal_owner_only(self):
        a = _agent("personal", owner_id="alice")
        assert _can("alice", "viewer", a, set()) is True
        assert _can("bob", "viewer", a, set()) is False

    def test_shared_requires_membership(self):
        a = _agent("shared", namespace="sales")
        assert _can("alice", "viewer", a, {"sales"}) is True
        assert _can("bob", "viewer", a, {"legal"}) is False
        assert _can("carol", "viewer", a, set()) is False

    def test_shared_default_namespace(self):
        a = _agent("shared", namespace="default")
        assert _can("alice", "viewer", a, {"default"}) is True
        assert _can("alice", "viewer", a, set()) is False

    def test_client_is_operator_or_admin_only(self):
        a = _agent("client", owner_id="acme")
        assert _can("x", "operator", a, set()) is True
        assert _can("x", "viewer", a, set()) is False
