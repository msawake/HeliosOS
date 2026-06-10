"""Tests for the Postgres-backed kernel policy stores.

Uses an in-process fake DB client that mimics the minimal psycopg surface the
stores touch (`tenant()` ctx → `execute`/`execute_one`/`commit`, `is_connected`),
so the suite stays DB-free like the rest. The live-Postgres round-trip is
exercised manually during development; this locks in serialization, cache
behavior, and schema-drift tolerance.
"""

import json

import pytest

from src.platform.namespace_policy import (
    GlobalPolicy,
    NamespacePolicy,
    PostgresGlobalPolicyStore,
    PostgresNamespacePolicyStore,
    _reconstruct,
)


class _FakeConn:
    """Interprets just the handful of statements the stores issue."""

    def __init__(self, ns_rows: dict, global_rows: dict, counters: dict):
        self._ns = ns_rows          # (tenant, namespace) -> policy_json str
        self._g = global_rows       # tenant -> policy_json str
        self._counters = counters

    def execute(self, query: str, params=None):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO namespace_policies"):
            tenant, ns, body = params
            self._ns[(tenant, ns)] = body
            return 1
        if q.startswith("DELETE FROM namespace_policies"):
            tenant, ns = params
            return 1 if self._ns.pop((tenant, ns), None) is not None else 0
        if q.startswith("SELECT policy_json FROM namespace_policies") and "namespace = %s" not in q:
            (tenant,) = params
            return [{"policy_json": v} for (t, _), v in self._ns.items() if t == tenant]
        if q.startswith("INSERT INTO global_policies"):
            tenant, body = params
            self._g[tenant] = body
            return 1
        raise AssertionError(f"unexpected execute: {q}")

    def execute_one(self, query: str, params=None):
        q = " ".join(query.split())
        if q.startswith("SELECT policy_json FROM namespace_policies") and "namespace = %s" in q:
            self._counters["ns_get"] = self._counters.get("ns_get", 0) + 1
            tenant, ns = params
            body = self._ns.get((tenant, ns))
            return {"policy_json": body} if body is not None else None
        if q.startswith("SELECT policy_json FROM global_policies"):
            (tenant,) = params
            body = self._g.get(tenant)
            return {"policy_json": body} if body is not None else None
        raise AssertionError(f"unexpected execute_one: {q}")

    def commit(self):
        pass


class _FakeDB:
    is_connected = True

    def __init__(self):
        self._ns: dict = {}
        self._g: dict = {}
        self.counters: dict = {}

    def tenant(self, tenant_id):
        conn = _FakeConn(self._ns, self._g, self.counters)

        class _Ctx:
            def __enter__(self_):
                return conn

            def __exit__(self_, *a):
                return False

        return _Ctx()


@pytest.fixture
def db():
    return _FakeDB()


class TestPostgresNamespacePolicyStore:
    def test_apply_get_roundtrip(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="legal", denied_tools=["shell__exec"],
                                    required_audit_level="full", max_agents=5))
        # fresh instance → empty cache → reads through to the fake DB
        fresh = PostgresNamespacePolicyStore(db, tenant_id="t1")
        p = fresh.get("legal")
        assert p is not None
        assert p.denied_tools == ["shell__exec"]
        assert p.required_audit_level == "full"
        assert p.max_agents == 5

    def test_stored_as_valid_json(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="ops", allowed_tools=["company__*"]))
        raw = db._ns[("t1", "ops")]
        assert json.loads(raw)["allowed_tools"] == ["company__*"]

    def test_cache_serves_repeat_reads(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="legal"))
        store.get("legal")
        store.get("legal")
        store.get("legal")
        # apply() invalidated the entry; the 3 gets share one DB read via cache
        assert db.counters.get("ns_get", 0) == 1

    def test_apply_invalidates_cache(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="legal", max_agents=1))
        assert store.get("legal").max_agents == 1
        store.apply(NamespacePolicy(namespace="legal", max_agents=9))
        assert store.get("legal").max_agents == 9

    def test_delete(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="legal"))
        assert store.delete("legal") is True
        assert store.get("legal") is None
        assert store.delete("legal") is False

    def test_list_all(self, db):
        store = PostgresNamespacePolicyStore(db, tenant_id="t1")
        store.apply(NamespacePolicy(namespace="legal"))
        store.apply(NamespacePolicy(namespace="ops"))
        names = {p.namespace for p in store.list_all()}
        assert names == {"legal", "ops"}

    def test_tenant_isolation(self, db):
        a = PostgresNamespacePolicyStore(db, tenant_id="t1")
        b = PostgresNamespacePolicyStore(db, tenant_id="t2")
        a.apply(NamespacePolicy(namespace="legal", max_agents=3))
        assert b.get("legal") is None  # other tenant cannot see it
        assert b.list_all() == []

    def test_unavailable_without_db(self):
        class _Down:
            is_connected = False
        store = PostgresNamespacePolicyStore(_Down(), tenant_id="t1")
        assert store.get("legal") is None
        assert store.list_all() == []
        with pytest.raises(RuntimeError):
            store.apply(NamespacePolicy(namespace="legal"))


class TestPostgresGlobalPolicyStore:
    def test_put_get_roundtrip(self, db):
        store = PostgresGlobalPolicyStore(db, tenant_id="t1")
        store.put(GlobalPolicy(denied_tools=["git__commit_push"], max_a2a_depth=3, pii_policy="redact"))
        fresh = PostgresGlobalPolicyStore(db, tenant_id="t1")
        gp = fresh.get()
        assert gp is not None
        assert gp.denied_tools == ["git__commit_push"]
        assert gp.max_a2a_depth == 3
        assert gp.pii_policy == "redact"

    def test_get_none_when_unset(self, db):
        assert PostgresGlobalPolicyStore(db, tenant_id="t1").get() is None


class TestReconstruct:
    def test_ignores_unknown_keys(self):
        p = _reconstruct(NamespacePolicy, {"namespace": "x", "denied_tools": ["a"], "bogus": 1})
        assert p.namespace == "x" and p.denied_tools == ["a"]

    def test_missing_keys_use_defaults(self):
        g = _reconstruct(GlobalPolicy, {"max_a2a_depth": 7})
        assert g.max_a2a_depth == 7
        assert g.pii_policy == "detect"  # default preserved
