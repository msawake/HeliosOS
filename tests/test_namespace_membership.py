"""Tests for NamespaceMemberStore + is_effective_member (src/platform/namespace_admins.py).

Exercises the store's tenant()→execute/execute_one/commit plumbing against a fake
in-memory DB (mirrors tests/test_postgres_policy_store.py), plus the admin∪member
union in is_effective_member. With no DB wired the store degrades to empty/no-op.
"""

from __future__ import annotations

import pytest

from src.platform.namespace_admins import (
    NamespaceAdminStore,
    NamespaceMemberStore,
    is_effective_member,
)

pytestmark = pytest.mark.kernel


class _FakeConn:
    """Table-agnostic fake for the namespace_members / namespace_admins stores
    (identical (tenant_id, namespace, user_id) schema + SQL shape)."""

    def __init__(self, rows: set):
        self._rows = rows  # set of (tenant_id, namespace, user_id)

    def execute(self, query: str, params=None):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO"):
            self._rows.add(tuple(params))
            return None
        if q.startswith("DELETE FROM"):
            self._rows.discard(tuple(params))
            return None
        if q.startswith("SELECT user_id FROM"):
            tenant, ns = params
            return [{"user_id": u} for (t, n, u) in sorted(self._rows) if t == tenant and n == ns]
        if q.startswith("SELECT namespace FROM"):
            tenant, uid = params
            return [{"namespace": n} for (t, n, u) in sorted(self._rows) if t == tenant and u == uid]
        raise AssertionError(f"unexpected execute: {q}")

    def execute_one(self, query: str, params=None):
        q = " ".join(query.split())
        if q.startswith("SELECT 1 FROM"):
            return {"?": 1} if tuple(params) in self._rows else None
        raise AssertionError(f"unexpected execute_one: {q}")

    def commit(self):
        pass


class _FakeDB:
    is_connected = True

    def __init__(self):
        self._rows: set = set()

    def tenant(self, tenant_id):
        conn = _FakeConn(self._rows)

        class _Ctx:
            def __enter__(self_):
                return conn

            def __exit__(self_, *a):
                return False

        return _Ctx()


@pytest.fixture
def member_store():
    return NamespaceMemberStore(db_client=_FakeDB(), tenant_id="t1")


class TestMemberStore:
    def test_add_is_member_remove(self, member_store):
        assert member_store.is_member("alice", "sales") is False
        assert member_store.add("sales", "alice") is True
        assert member_store.is_member("alice", "sales") is True
        # idempotent
        assert member_store.add("sales", "alice") is True
        assert member_store.remove("sales", "alice") is True
        assert member_store.is_member("alice", "sales") is False

    def test_list_for_namespace(self, member_store):
        member_store.add("sales", "alice")
        member_store.add("sales", "bob")
        member_store.add("legal", "carol")
        assert member_store.list_for_namespace("sales") == ["alice", "bob"]
        assert member_store.list_for_namespace("legal") == ["carol"]

    def test_namespaces_for_user(self, member_store):
        member_store.add("sales", "alice")
        member_store.add("legal", "alice")
        member_store.add("sales", "bob")
        assert member_store.namespaces_for_user("alice") == ["legal", "sales"]
        assert member_store.namespaces_for_user("bob") == ["sales"]

    def test_unwired_db_is_noop(self):
        store = NamespaceMemberStore(db_client=None, tenant_id="t1")
        assert store.available is False
        assert store.is_member("alice", "sales") is False
        assert store.add("sales", "alice") is False
        assert store.namespaces_for_user("alice") == []


class TestEffectiveMember:
    def test_member_is_effective(self):
        db = _FakeDB()
        member = NamespaceMemberStore(db_client=db, tenant_id="t1")
        admin = NamespaceAdminStore(db_client=_FakeDB(), tenant_id="t1")
        member.add("sales", "alice")
        assert is_effective_member("alice", "sales", member_store=member, admin_store=admin) is True

    def test_admin_is_effective_without_membership(self):
        # A namespace admin counts as a member even without a members row.
        admin_db = _FakeDB()
        admin = NamespaceAdminStore(db_client=admin_db, tenant_id="t1")
        member = NamespaceMemberStore(db_client=_FakeDB(), tenant_id="t1")
        admin.grant("sales", "carol")
        assert is_effective_member("carol", "sales", member_store=member, admin_store=admin) is True

    def test_non_member_non_admin_is_not_effective(self):
        member = NamespaceMemberStore(db_client=_FakeDB(), tenant_id="t1")
        admin = NamespaceAdminStore(db_client=_FakeDB(), tenant_id="t1")
        assert is_effective_member("dave", "sales", member_store=member, admin_store=admin) is False
