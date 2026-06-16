"""Tests for three-tier scoped secrets (platform / namespace / user).

Covers `src/platform/credentials.py` scope naming + CredentialStore.{put_scoped_secret,
list_secrets, resolve, delete_scoped_secret} on top of a fake in-memory backend wired
into the real SecretsManager (so the get/put/list/delete plumbing is exercised end-to-end).
"""

from __future__ import annotations

import pytest

from src.core.secrets import SecretsManager
from src.platform.credentials import (
    CredentialStore,
    SCOPE_NAMESPACE,
    SCOPE_PLATFORM,
    SCOPE_USER,
    logical_secret_name,
    scoped_secret_name,
    validate_secret_name,
)

pytestmark = pytest.mark.kernel


class _FakeBackend:
    """In-memory stand-in for PostgresSecretBackend (column-aware)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    @property
    def available(self) -> bool:
        return True

    def get(self, name: str):
        r = self.rows.get(name)
        return r["value"] if r else None

    def put(self, name, value, *, user_id="default", kind="generic", scope="user", namespace=None):
        self.rows[name] = {
            "value": value, "user_id": user_id, "kind": kind,
            "scope": scope, "namespace": namespace,
        }
        return True

    def delete(self, name) -> bool:
        self.rows.pop(name, None)
        return True

    def list_names(self, *, scope="user", namespace=None, user_id=None):
        out = []
        for nm, r in self.rows.items():
            if r["scope"] != scope:
                continue
            if namespace is not None and r["namespace"] != namespace:
                continue
            if user_id is not None and r["user_id"] != user_id:
                continue
            out.append({"secret_name": nm, "kind": r["kind"], "scope": r["scope"],
                        "namespace": r["namespace"], "user_id": r["user_id"]})
        return out


def _store():
    sm = SecretsManager(db_backend=_FakeBackend())
    return CredentialStore(sm), sm


# --- naming -----------------------------------------------------------------

class TestNaming:
    def test_scoped_names(self):
        assert scoped_secret_name("k", scope=SCOPE_PLATFORM) == "forgeos-platform-k"
        assert scoped_secret_name("k", scope=SCOPE_NAMESPACE, namespace="sales") == "forgeos-ns-sales-k"
        assert scoped_secret_name("k", scope=SCOPE_USER, user_id="alice") == "forgeos-user-alice-k"

    def test_logical_roundtrip(self):
        for scope, kw in [
            (SCOPE_PLATFORM, {}),
            (SCOPE_NAMESPACE, {"namespace": "sales"}),
            (SCOPE_USER, {"user_id": "alice"}),
        ]:
            stored = scoped_secret_name("gw-key", scope=scope, **kw)
            assert logical_secret_name(stored, scope=scope, **kw) == "gw-key"

    def test_legacy_name_passthrough(self):
        # An unprefixed legacy name survives delogification unchanged.
        assert logical_secret_name(
            "forgeos-jira-token-alice", scope=SCOPE_USER, user_id="bob"
        ) == "forgeos-jira-token-alice"

    @pytest.mark.parametrize("bad", ["", "-leading", "has space", "sl/ash", "dot.ted", "x" * 250])
    def test_validate_rejects(self, bad):
        with pytest.raises(ValueError):
            validate_secret_name(bad)

    def test_validate_accepts(self):
        assert validate_secret_name("gw-key_1") == "gw-key_1"


# --- put / get / resolve ----------------------------------------------------

class TestResolve:
    def test_put_and_resolve_user(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "u-val", scope=SCOPE_USER, user_id="alice")
        assert cs.resolve("gw", namespace="sales", user_id="alice") == "u-val"

    def test_precedence_user_first_default(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "ns-val", scope=SCOPE_NAMESPACE, namespace="sales")
        cs.put_scoped_secret("gw", "u-val", scope=SCOPE_USER, user_id="alice")
        # default order is user → namespace → platform
        assert cs.resolve("gw", namespace="sales", user_id="alice") == "u-val"

    def test_precedence_namespace_first_for_mcp(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "ns-val", scope=SCOPE_NAMESPACE, namespace="sales")
        cs.put_scoped_secret("gw", "u-val", scope=SCOPE_USER, user_id="alice")
        got = cs.resolve(
            "gw", namespace="sales", user_id="alice",
            order=(SCOPE_NAMESPACE, SCOPE_USER, SCOPE_PLATFORM),
        )
        assert got == "ns-val"

    def test_namespace_fallback_to_user(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "u-val", scope=SCOPE_USER, user_id="alice")
        # No namespace cred → namespace-first walk falls through to user.
        got = cs.resolve(
            "gw", namespace="sales", user_id="alice",
            order=(SCOPE_NAMESPACE, SCOPE_USER, SCOPE_PLATFORM),
        )
        assert got == "u-val"

    def test_platform_last_resort(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "p-val", scope=SCOPE_PLATFORM)
        assert cs.resolve("gw", namespace="sales", user_id="alice") == "p-val"

    def test_explicit_pins(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "ns-val", scope=SCOPE_NAMESPACE, namespace="sales")
        cs.put_scoped_secret("gw", "u-val", scope=SCOPE_USER, user_id="alice")
        cs.put_scoped_secret("gw", "p-val", scope=SCOPE_PLATFORM)
        assert cs.resolve("ns/gw", namespace="sales", user_id="alice") == "ns-val"
        assert cs.resolve("user/gw", namespace="sales", user_id="alice") == "u-val"
        assert cs.resolve("platform/gw", namespace="sales", user_id="alice") == "p-val"

    def test_unresolved_returns_none(self):
        cs, _ = _store()
        assert cs.resolve("missing", namespace="sales", user_id="alice") is None

    def test_literal_legacy_fallback(self):
        cs, _ = _store()
        # Legacy literal name (per-user JIRA style) stored verbatim.
        cs.put_secret("forgeos-jira-token-alice", "tok", user_id="alice", kind="jira")
        assert cs.resolve("forgeos-jira-token-alice", user_id="alice") == "tok"

    def test_env_not_shadowing_scope_walk(self, monkeypatch):
        cs, _ = _store()
        # An env var matching a scoped candidate must NOT satisfy the walk
        # (allow_env=False while probing); only the literal fallback may use env.
        monkeypatch.setenv("FORGEOS_USER_ALICE_GW", "env-val")
        assert cs.resolve("gw", namespace="sales", user_id="alice") is None

    def test_namespace_scope_requires_namespace(self):
        cs, _ = _store()
        with pytest.raises(ValueError):
            cs.put_scoped_secret("gw", "v", scope=SCOPE_NAMESPACE)


# --- list / delete ----------------------------------------------------------

class TestListAndDelete:
    def test_list_returns_logical_names_not_values(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "secret-value-xyz", scope=SCOPE_NAMESPACE, namespace="sales", kind="llm")
        rows = cs.list_secrets(scope=SCOPE_NAMESPACE, namespace="sales")
        assert rows == [{"name": "gw", "kind": "llm", "scope": "namespace", "namespace": "sales"}]
        assert "secret-value-xyz" not in str(rows)

    def test_list_filters_by_scope_and_namespace(self):
        cs, _ = _store()
        cs.put_scoped_secret("a", "1", scope=SCOPE_USER, user_id="alice")
        cs.put_scoped_secret("b", "2", scope=SCOPE_NAMESPACE, namespace="sales")
        cs.put_scoped_secret("c", "3", scope=SCOPE_NAMESPACE, namespace="legal")
        cs.put_scoped_secret("d", "4", scope=SCOPE_PLATFORM)
        assert {r["name"] for r in cs.list_secrets(scope=SCOPE_USER, user_id="alice")} == {"a"}
        assert {r["name"] for r in cs.list_secrets(scope=SCOPE_NAMESPACE, namespace="sales")} == {"b"}
        assert {r["name"] for r in cs.list_secrets(scope=SCOPE_PLATFORM)} == {"d"}

    def test_delete(self):
        cs, _ = _store()
        cs.put_scoped_secret("gw", "v", scope=SCOPE_USER, user_id="alice")
        assert cs.resolve("user/gw", user_id="alice") == "v"
        cs.delete_scoped_secret("gw", scope=SCOPE_USER, user_id="alice")
        assert cs.resolve("user/gw", user_id="alice") is None

    def test_list_merges_postgres_and_gcp(self):
        # When a GCP project is configured, writes prefer Secret Manager — so
        # list_names must scan GCP (by scope prefix) AND merge Postgres rows.
        from src.platform.credentials import scoped_secret_name

        class _GcpSecret:
            def __init__(self, name):
                self.name = name

        class _GcpClient:
            def __init__(self, ids):
                self._ids = ids
            def list_secrets(self, request=None):
                return [_GcpSecret(f"projects/p/secrets/{i}") for i in self._ids]

        backend = _FakeBackend()
        # A legacy/local row that lives only in Postgres.
        backend.put(
            scoped_secret_name("pg-only", scope=SCOPE_NAMESPACE, namespace="sales"),
            "v", scope="namespace", namespace="sales",
        )
        sm = SecretsManager(db_backend=backend)
        sm._client = _GcpClient(["forgeos-ns-sales-gw", "forgeos-ns-sales-other", "forgeos-ns-legal-x"])
        sm._project_id = "p"
        cs = CredentialStore(sm)
        names = {r["name"] for r in cs.list_secrets(scope=SCOPE_NAMESPACE, namespace="sales")}
        assert {"pg-only", "gw", "other"} <= names  # Postgres + GCP merged
        assert "x" not in names  # different namespace filtered out by prefix
