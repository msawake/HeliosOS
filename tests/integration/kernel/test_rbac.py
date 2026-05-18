"""Tests for platform RBAC — role-based access control."""

import pytest

from src.platform.rbac import RBACBinding, RBACError, RBACManager, Role


@pytest.fixture
def manager():
    return RBACManager(bindings=[
        RBACBinding(identity="admin@company.com", role=Role.ADMIN, namespaces=["*"]),
        RBACBinding(identity="sales-dev@company.com", role=Role.DEVELOPER, namespaces=["sales", "marketing"]),
        RBACBinding(identity="ops@company.com", role=Role.OPERATOR, namespaces=["*"]),
        RBACBinding(identity="viewer@company.com", role=Role.VIEWER, namespaces=["*"]),
        RBACBinding(identity="finance-owner@company.com", role=Role.NAMESPACE_OWNER, namespaces=["finance"]),
    ])


class TestRBAC:
    def test_admin_can_do_anything(self, manager):
        assert manager.check("admin@company.com", "agent.deploy", "finance") is True
        assert manager.check("admin@company.com", "fleet.quarantine", "sales") is True
        assert manager.check("admin@company.com", "policy.apply", "ops") is True

    def test_developer_can_deploy_in_assigned_namespace(self, manager):
        assert manager.check("sales-dev@company.com", "agent.deploy", "sales") is True
        assert manager.check("sales-dev@company.com", "agent.invoke", "marketing") is True

    def test_developer_cannot_deploy_in_other_namespace(self, manager):
        assert manager.check("sales-dev@company.com", "agent.deploy", "finance") is False

    def test_developer_cannot_quarantine(self, manager):
        assert manager.check("sales-dev@company.com", "fleet.quarantine", "sales") is False

    def test_operator_can_quarantine_anywhere(self, manager):
        assert manager.check("ops@company.com", "fleet.quarantine", "sales") is True
        assert manager.check("ops@company.com", "fleet.evict", "finance") is True

    def test_operator_cannot_deploy(self, manager):
        assert manager.check("ops@company.com", "agent.deploy", "sales") is False

    def test_viewer_can_only_read(self, manager):
        assert manager.check("viewer@company.com", "fleet.read", "sales") is True
        assert manager.check("viewer@company.com", "audit.read", "finance") is True
        assert manager.check("viewer@company.com", "agent.deploy", "sales") is False
        assert manager.check("viewer@company.com", "agent.invoke", "sales") is False

    def test_namespace_owner_scoped(self, manager):
        assert manager.check("finance-owner@company.com", "agent.deploy", "finance") is True
        assert manager.check("finance-owner@company.com", "policy.apply", "finance") is True
        assert manager.check("finance-owner@company.com", "agent.deploy", "sales") is False

    def test_unknown_identity_denied(self, manager):
        assert manager.check("hacker@evil.com", "agent.deploy", "sales") is False

    def test_require_raises_on_deny(self, manager):
        with pytest.raises(RBACError, match="Access denied"):
            manager.require("viewer@company.com", "agent.deploy", "sales")

    def test_require_passes_on_allow(self, manager):
        manager.require("admin@company.com", "agent.deploy", "sales")

    def test_disabled_manager_allows_all(self):
        m = RBACManager(enabled=False)
        assert m.check("anyone", "anything", "anywhere") is True

    def test_add_and_remove_binding(self):
        m = RBACManager()
        m.add_binding(RBACBinding(identity="new@co.com", role=Role.DEVELOPER, namespaces=["test"]))
        assert m.check("new@co.com", "agent.deploy", "test") is True

        m.remove_binding("new@co.com")
        assert m.check("new@co.com", "agent.deploy", "test") is False

    def test_from_config(self):
        config = [
            {"identity": "user@co.com", "role": "developer", "namespaces": ["sales"]},
            {"identity": "admin@co.com", "role": "admin"},
        ]
        m = RBACManager.from_config(config)
        assert m.check("user@co.com", "agent.deploy", "sales") is True
        assert m.check("admin@co.com", "fleet.quarantine", "ops") is True

    def test_get_identity_permissions(self, manager):
        perms = manager.get_identity_permissions("sales-dev@company.com")
        assert "developer" in perms["roles"]
        assert "sales" in perms["namespaces"]
        assert "agent.deploy" in perms["permissions"]
