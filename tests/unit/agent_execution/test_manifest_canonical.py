"""Tests for src/forgeos_sdk/manifest.py — canonical dict + read_v2_section helper.

This covers Phase 1 #5's forward-compatible step: canonical_dict() surfaces v2
sections as first-class top-level keys (no `_memory`/`_guardrails` bag),
and read_v2_section() transparently reads from either shape so 16 existing
consumers migrate incrementally without breaking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.forgeos_sdk.manifest import (
    AgentManifest,
    Budgets,
    Boundaries,
    Capabilities,
    DataBoundaries,
    Governance,
    Guardrails,
    LLMConfig,
    Lifecycle,
    Metadata,
    PolicyRef,
    Spec,
    ToolACL,
    read_v2_section,
)


def _make_manifest(**overrides) -> AgentManifest:
    metadata = overrides.pop("metadata", Metadata(name="alpha", namespace="ns", description="t"))
    spec = overrides.pop(
        "spec",
        Spec(
            llm=LLMConfig(chat_model="claude-sonnet-4-5-20250514"),
            tools=["mcp__filesystem__*"],
        ),
    )
    return AgentManifest(
        apiVersion="agentos/v1",
        kind="AgentContract",
        metadata=metadata,
        spec=spec,
        **overrides,
    )


# ---------------------------------------------------------------------------
# canonical_dict
# ---------------------------------------------------------------------------


class TestCanonicalDict:
    def test_shape_top_level_keys(self):
        m = _make_manifest()
        out = m.canonical_dict()
        assert set(out.keys()) == {"apiVersion", "kind", "metadata", "spec"}
        assert out["apiVersion"] == "agentos/v1"
        assert out["kind"] == "AgentContract"

    def test_v2_sections_surface_first_class(self):
        spec = Spec(
            llm=LLMConfig(chat_model="gpt-4o"),
            tools=[],
            guardrails=Guardrails(max_tokens_per_run=500, max_cost_usd_per_day=1.5),
            boundaries=Boundaries(
                budgets=Budgets(daily_usd=2.0, per_task_usd=0.5),
                data=DataBoundaries(allowed_namespaces=["sales"], pii_policy="mask"),
            ),
            capabilities=Capabilities(tools=ToolACL(allowed=["mcp__x"], denied=["mcp__y"])),
            governance=Governance(policies=[PolicyRef(name="p1", ref="./p.rego")]),
        )
        m = _make_manifest(spec=spec)
        out = m.canonical_dict()

        # v2 sections are at spec.X, NOT spec.metadata._X
        assert "guardrails" in out["spec"]
        assert out["spec"]["guardrails"]["max_cost_usd_per_day"] == 1.5
        assert out["spec"]["boundaries"]["budgets"]["daily_usd"] == 2.0
        assert out["spec"]["boundaries"]["data"]["pii_policy"] == "mask"
        assert out["spec"]["capabilities"]["tools"]["denied"] == ["mcp__y"]
        assert out["spec"]["governance"]["policies"][0]["name"] == "p1"

        # And the legacy bag keys are NOT present in canonical output.
        assert "_guardrails" not in out["spec"].get("metadata", {})
        assert "_boundaries" not in out["spec"].get("metadata", {})

    def test_v2_lifecycle_overrides_flat_execution_type(self):
        spec = Spec(
            stack="forgeos",
            execution_type="reflex",
            llm=LLMConfig(chat_model="gpt-4o"),
            tools=[],
            lifecycle=Lifecycle(type="scheduled", schedule="0 * * * *"),
        )
        m = _make_manifest(spec=spec)
        out = m.canonical_dict()
        # The effective execution_type is taken from lifecycle.type
        assert out["spec"]["execution_type"] == "scheduled"
        assert out["spec"]["schedule"] == "0 * * * *"

    def test_tools_pulled_from_capabilities_when_present(self):
        spec = Spec(
            llm=LLMConfig(chat_model="gpt-4o"),
            tools=["legacy-tool"],
            capabilities=Capabilities(tools=ToolACL(allowed=["new-tool"])),
        )
        m = _make_manifest(spec=spec)
        out = m.canonical_dict()
        assert out["spec"]["tools"] == ["new-tool"]

    def test_roundtrip_wire_compat_still_works(self):
        """canonical_dict does not break the legacy to_deploy_request path."""
        m = _make_manifest(
            spec=Spec(
                llm=LLMConfig(chat_model="claude-sonnet-4-5-20250514"),
                tools=["t"],
                guardrails=Guardrails(max_tokens_per_run=100),
            )
        )
        legacy = m.to_deploy_request()
        canonical = m.canonical_dict()
        # Both paths emit the same effective tools and agent name.
        assert legacy["name"] == canonical["metadata"]["name"]
        assert legacy["tools"] == canonical["spec"]["tools"]
        # Legacy path still emits the bag (unchanged).
        assert "_guardrails" in legacy["metadata"]
        # Canonical path does NOT.
        assert "_guardrails" not in canonical["spec"].get("metadata", {})


# ---------------------------------------------------------------------------
# read_v2_section
# ---------------------------------------------------------------------------


class TestReadV2SectionFromDict:
    def test_reads_first_class_when_present(self):
        source = {
            "capabilities": {"tools": {"allowed": ["x"]}},
            "metadata": {"_capabilities": {"tools": {"allowed": ["legacy-x"]}}},
        }
        # First-class wins over legacy bag.
        assert read_v2_section(source, "capabilities")["tools"]["allowed"] == ["x"]

    def test_falls_back_to_legacy_bag(self):
        source = {"metadata": {"_capabilities": {"tools": {"denied": ["z"]}}}}
        assert read_v2_section(source, "capabilities")["tools"]["denied"] == ["z"]

    def test_returns_default_when_absent(self):
        assert read_v2_section({}, "capabilities", default={"tools": {}}) == {"tools": {}}

    def test_handles_nested_spec_shape(self):
        source = {"spec": {"governance": {"policies": [{"name": "p"}]}}}
        assert read_v2_section(source, "governance")["policies"][0]["name"] == "p"

    def test_reads_namespace_from_bag(self):
        source = {"metadata": {"_namespace": "sales"}}
        assert read_v2_section(source, "namespace", default="default") == "sales"

    def test_unknown_section_raises(self):
        with pytest.raises(KeyError, match="unknown v2 section"):
            read_v2_section({}, "nope")


class TestReadV2SectionFromObject:
    def test_reads_first_class_attribute(self):
        # Object with first-class `capabilities` attribute.
        obj = SimpleNamespace(
            capabilities={"tools": {"allowed": ["x"]}},
            metadata={"_capabilities": {"tools": {"allowed": ["legacy"]}}},
        )
        assert read_v2_section(obj, "capabilities")["tools"]["allowed"] == ["x"]

    def test_falls_back_to_object_metadata_bag(self):
        # Simulates AgentDefinition shape (no first-class v2 attrs, only metadata bag).
        obj = SimpleNamespace(metadata={"_governance": {"policies": [{"name": "pp"}]}})
        assert read_v2_section(obj, "governance")["policies"][0]["name"] == "pp"

    def test_none_first_class_falls_through_to_bag(self):
        obj = SimpleNamespace(
            capabilities=None,  # explicitly absent — must fall back
            metadata={"_capabilities": {"tools": {"denied": ["n"]}}},
        )
        assert read_v2_section(obj, "capabilities")["tools"]["denied"] == ["n"]

    def test_default_when_object_has_no_metadata(self):
        obj = SimpleNamespace()
        assert read_v2_section(obj, "capabilities", default={}) == {}
