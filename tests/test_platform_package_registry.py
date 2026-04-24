"""Tests for src/platform/package_registry.py — content-addressed agent packages."""

from __future__ import annotations


import pytest

from src.platform.package_registry import (
    FilesystemPackageRegistry,
    InvalidDigest,
    Package,
    PackageNotFound,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(name: str = "alpha", version: str = "1.0.0", namespace: str = "ns") -> dict:
    return {
        "apiVersion": "agentos/v1",
        "kind": "AgentContract",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "version": version,
            "description": "test",
        },
        "spec": {
            "stack": "forgeos",
            "execution_type": "reflex",
            "llm": {"chat_model": "claude-sonnet-4-5-20250514", "provider": "anthropic"},
            "tools": [],
        },
    }


# ---------------------------------------------------------------------------
# Digest stability
# ---------------------------------------------------------------------------


class TestDigestStability:
    def test_same_manifest_same_digest(self):
        a = Package.compute_digest(_manifest(), None)
        b = Package.compute_digest(_manifest(), None)
        assert a == b

    def test_different_manifest_different_digest(self):
        a = Package.compute_digest(_manifest(version="1.0.0"), None)
        b = Package.compute_digest(_manifest(version="1.0.1"), None)
        assert a != b

    def test_key_order_does_not_affect_digest(self):
        m1 = _manifest()
        m2 = dict(reversed(list(m1.items())))  # same data, different iteration order
        # Nested dicts are still the same — canonical JSON sorts them.
        assert Package.compute_digest(m1, None) == Package.compute_digest(m2, None)

    def test_files_content_affects_digest(self, tmp_path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "prompt.md").write_text("Version 1")
        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "prompt.md").write_text("Version 2")
        digest_a = Package.compute_digest(_manifest(), d1)
        digest_b = Package.compute_digest(_manifest(), d2)
        assert digest_a != digest_b

    def test_files_dir_ignores_pycache(self, tmp_path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "prompt.md").write_text("hi")
        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "prompt.md").write_text("hi")
        (d2 / "__pycache__").mkdir()
        (d2 / "__pycache__" / "stale.pyc").write_bytes(b"junk")
        assert Package.compute_digest(_manifest(), d1) == Package.compute_digest(_manifest(), d2)

    def test_digest_format(self):
        d = Package.compute_digest(_manifest(), None)
        assert d.startswith("sha256:")
        assert len(d) == len("sha256:") + 64


# ---------------------------------------------------------------------------
# Registry push / pull / resolve
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path) -> FilesystemPackageRegistry:
    return FilesystemPackageRegistry(root=tmp_path / "registry")


class TestPushPull:
    def test_push_returns_digest_and_creates_dir(self, registry, tmp_path):
        pkg = Package(manifest=_manifest())
        digest = registry.push(pkg, pushed_by="tester")
        assert digest.startswith("sha256:")
        # The on-disk layout uses sha256_ (filesystem-safe).
        target = registry.root / digest.replace("sha256:", "sha256_")
        assert (target / "manifest.json").exists()
        assert (target / "meta.json").exists()

    def test_pull_returns_manifest(self, registry):
        pkg = Package(manifest=_manifest())
        digest = registry.push(pkg)
        pulled = registry.pull(digest)
        assert pulled.manifest == pkg.manifest
        assert pulled.digest == digest

    def test_push_includes_files_dir(self, registry, tmp_path):
        files = tmp_path / "files"
        files.mkdir()
        (files / "prompt.md").write_text("You are a scout.")
        pkg = Package(manifest=_manifest(), files_dir=files)
        digest = registry.push(pkg)
        pulled = registry.pull(digest)
        assert pulled.files_dir is not None
        assert (pulled.files_dir / "prompt.md").read_text() == "You are a scout."

    def test_pull_invalid_digest_raises(self, registry):
        with pytest.raises(InvalidDigest):
            registry.pull("not-a-digest")

    def test_pull_missing_digest_raises(self, registry):
        with pytest.raises(PackageNotFound):
            registry.pull("sha256:" + "0" * 64)

    def test_duplicate_push_is_idempotent(self, registry):
        pkg = Package(manifest=_manifest())
        a = registry.push(pkg)
        b = registry.push(pkg)
        assert a == b
        # One digest directory, one index entry.
        assert len(registry.list_digests()) == 1
        assert len(registry.list()) == 1


class TestVersioning:
    def test_version_must_be_semver(self, registry):
        pkg = Package(manifest=_manifest(version="alpha"))
        with pytest.raises(ValueError, match="semver"):
            registry.push(pkg)

    def test_resolve_returns_current_digest(self, registry):
        pkg1 = Package(manifest=_manifest(version="1.0.0"))
        digest1 = registry.push(pkg1)
        assert registry.resolve("ns", "alpha", "1.0.0") == digest1

    def test_resolve_missing_version_raises(self, registry):
        with pytest.raises(PackageNotFound):
            registry.resolve("ns", "alpha", "9.9.9")

    def test_index_retains_both_versions(self, registry):
        registry.push(Package(manifest=_manifest(version="1.0.0")))
        registry.push(Package(manifest=_manifest(version="1.1.0")))
        entries = registry.list()
        assert {e["version"] for e in entries} == {"1.0.0", "1.1.0"}

    def test_rollback_by_digest_round_trip(self, registry):
        """The plan's acceptance criterion: deploy v1, deploy v2, rollback to v1 by SHA."""
        v1 = registry.push(Package(manifest=_manifest(version="1.0.0")))
        v2 = registry.push(Package(manifest=_manifest(version="1.1.0")))
        assert v1 != v2
        # Rollback = pull by the original digest.
        rolled_back = registry.pull(v1)
        assert rolled_back.version == "1.0.0"
        assert rolled_back.digest == v1

    def test_list_filters_by_name(self, registry):
        registry.push(Package(manifest=_manifest(name="a", version="1.0.0")))
        registry.push(Package(manifest=_manifest(name="b", version="1.0.0")))
        names = {e["name"] for e in registry.list(name="a")}
        assert names == {"a"}


class TestListDigests:
    def test_list_digests_survives_corrupt_index(self, registry):
        digest = registry.push(Package(manifest=_manifest()))
        # Corrupt the index.
        (registry.root / "index.json").write_text("not json")
        # pull by digest still works; list() falls back gracefully.
        assert registry.pull(digest).digest == digest
        assert digest in registry.list_digests()
        # list(index-based) returns [] when index is corrupt — acceptable degradation.
        assert registry.list() == []


# ---------------------------------------------------------------------------
# Package helpers
# ---------------------------------------------------------------------------


class TestPackageHelpers:
    def test_qualified_name(self):
        pkg = Package(manifest=_manifest(name="scout", namespace="sales"))
        assert pkg.qualified_name == "sales/scout"

    def test_finalize_sets_digest(self):
        pkg = Package(manifest=_manifest())
        assert pkg.digest == ""
        d = pkg.finalize()
        assert d == pkg.digest and d.startswith("sha256:")
