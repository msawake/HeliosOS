"""Tests for A2A context isolation (Phase 1c)."""
import pytest
from src.platform.a2a import IsolationPolicy, IsolatedResult


class TestIsolationPolicy:
    def test_default_is_isolated(self):
        p = IsolationPolicy()
        assert p.inherit_history is False
        assert p.inherit_context is False
        assert p.max_result_chars == 50_000

    def test_legacy_passes_everything(self):
        p = IsolationPolicy._legacy()
        assert p.inherit_history is True
        assert p.inherit_context is True
        assert p.max_result_chars == 0

    def test_isolated_factory(self):
        p = IsolationPolicy.isolated()
        assert p.inherit_history is False
        assert p.inherit_context is False

    def test_from_manifest_no_config(self):
        """No isolation config in manifest -> legacy behavior."""
        class FakeDef:
            metadata = {}
        p = IsolationPolicy.from_manifest(FakeDef())
        assert p.inherit_history is True  # legacy
        assert p.inherit_context is True

    def test_from_manifest_with_config(self):
        class FakeDef:
            capabilities = {
                "a2a": {
                    "isolation": {
                        "inherit_history": False,
                        "inherit_context": False,
                        "max_result_chars": 10_000,
                    }
                }
            }
            metadata = {}
        p = IsolationPolicy.from_manifest(FakeDef())
        assert p.inherit_history is False
        assert p.inherit_context is False
        assert p.max_result_chars == 10_000

    def test_from_manifest_partial_config(self):
        class FakeDef:
            capabilities = {
                "a2a": {
                    "isolation": {"inherit_context": True}
                }
            }
            metadata = {}
        p = IsolationPolicy.from_manifest(FakeDef())
        assert p.inherit_context is True
        assert p.inherit_history is False  # default


class TestIsolatedResult:
    def test_creation(self):
        r = IsolatedResult(output="hello", status="completed", tokens_used=100)
        assert r.output == "hello"
        assert r.error is None

    def test_with_error(self):
        r = IsolatedResult(output="", status="failed", tokens_used=0, error="timeout")
        assert r.error == "timeout"


class TestResultTruncation:
    def test_truncation(self):
        policy = IsolationPolicy(max_result_chars=10)
        output = "a" * 100
        if policy.max_result_chars > 0 and len(output) > policy.max_result_chars:
            output = output[:policy.max_result_chars] + "\n... [truncated]"
        assert len(output) < 100
        assert output.endswith("[truncated]")

    def test_no_truncation_when_zero(self):
        policy = IsolationPolicy(max_result_chars=0)
        output = "a" * 100
        if policy.max_result_chars > 0 and len(output) > policy.max_result_chars:
            output = output[:policy.max_result_chars] + "\n... [truncated]"
        assert len(output) == 100

    def test_no_truncation_when_under_limit(self):
        policy = IsolationPolicy(max_result_chars=200)
        output = "a" * 50
        if policy.max_result_chars > 0 and len(output) > policy.max_result_chars:
            output = output[:policy.max_result_chars] + "\n... [truncated]"
        assert len(output) == 50
