"""Tests for src/platform/a2a_contracts.py — typed A2A contracts (Phase 2 #4)."""

from __future__ import annotations

import pytest

from src.platform.a2a_contracts import (
    A2AContract,
    A2AMethod,
    A2AValidator,
    ContractRegistry,
    MethodNotFound,
    SchemaMismatch,
)


# ---------------------------------------------------------------------------
# A2AMethod / A2AContract dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_method_roundtrip(self):
        m = A2AMethod(
            name="score_lead",
            description="Score a lead 0-100",
            input_schema={"type": "object", "required": ["lead"]},
            output_schema={"type": "object"},
            idempotent=True,
            timeout_seconds=30.0,
        )
        assert A2AMethod.from_dict(m.to_dict()) == m

    def test_contract_qualified_name(self):
        c = A2AContract(namespace="sales", name="scorer")
        assert c.qualified_name == "sales/scorer"

    def test_method_lookup(self):
        c = A2AContract(
            namespace="sales",
            name="scorer",
            methods={"score": A2AMethod(name="score")},
        )
        assert c.method("score") is not None
        assert c.method("unknown") is None


class TestContractFromManifest:
    def test_returns_none_when_no_methods_declared(self):
        manifest = {
            "metadata": {"namespace": "n", "name": "a"},
            "spec": {"capabilities": {"a2a": {}}},
        }
        assert A2AContract.from_manifest(manifest) is None

    def test_extracts_methods(self):
        manifest = {
            "metadata": {"namespace": "sales", "name": "scorer"},
            "spec": {
                "capabilities": {
                    "a2a": {
                        "methods": [
                            {
                                "name": "score_lead",
                                "input_schema": {"type": "object", "required": ["id"]},
                                "idempotent": True,
                            }
                        ]
                    }
                }
            },
        }
        contract = A2AContract.from_manifest(manifest)
        assert contract is not None
        assert contract.qualified_name == "sales/scorer"
        m = contract.method("score_lead")
        assert m is not None
        assert m.idempotent is True
        assert m.input_schema["required"] == ["id"]


# ---------------------------------------------------------------------------
# A2AValidator — minimal JSON-schema subset
# ---------------------------------------------------------------------------


class TestValidatorTypes:
    def test_string_type(self):
        assert A2AValidator.validate("hi", {"type": "string"}) == []
        errs = A2AValidator.validate(42, {"type": "string"})
        assert errs and "expected string" in errs[0]

    def test_integer_type_rejects_bool(self):
        errs = A2AValidator.validate(True, {"type": "integer"})
        assert errs and "boolean" in errs[0]

    def test_number_accepts_int_and_float(self):
        assert A2AValidator.validate(1, {"type": "number"}) == []
        assert A2AValidator.validate(1.5, {"type": "number"}) == []

    def test_boolean_type(self):
        assert A2AValidator.validate(True, {"type": "boolean"}) == []
        assert A2AValidator.validate(1, {"type": "boolean"}) != []

    def test_array_type(self):
        assert A2AValidator.validate([1, 2], {"type": "array"}) == []
        assert A2AValidator.validate("no", {"type": "array"}) != []

    def test_null_type(self):
        assert A2AValidator.validate(None, {"type": "null"}) == []
        assert A2AValidator.validate(0, {"type": "null"}) != []


class TestValidatorRequired:
    def test_required_field_missing(self):
        schema = {"type": "object", "required": ["id"]}
        errs = A2AValidator.validate({}, schema)
        assert errs and "missing required field 'id'" in errs[0]

    def test_required_field_present(self):
        schema = {"type": "object", "required": ["id"]}
        assert A2AValidator.validate({"id": 1}, schema) == []


class TestValidatorProperties:
    def test_nested_property_validated(self):
        schema = {
            "type": "object",
            "properties": {"user": {"type": "object", "required": ["name"]}},
        }
        errs = A2AValidator.validate({"user": {}}, schema)
        assert errs and "missing required field 'name'" in errs[0]
        assert "$.user" in errs[0]


class TestValidatorItems:
    def test_array_item_type(self):
        schema = {"type": "array", "items": {"type": "integer"}}
        errs = A2AValidator.validate([1, "two", 3], schema)
        assert len(errs) == 1
        assert "[1]" in errs[0]


class TestValidatorBounds:
    def test_min_length(self):
        errs = A2AValidator.validate("ab", {"type": "string", "minLength": 3})
        assert errs and "minLength" in errs[0]

    def test_max_length(self):
        errs = A2AValidator.validate("abcdef", {"type": "string", "maxLength": 3})
        assert errs and "maxLength" in errs[0]

    def test_minimum(self):
        errs = A2AValidator.validate(1, {"type": "integer", "minimum": 5})
        assert errs and "minimum" in errs[0]

    def test_maximum(self):
        errs = A2AValidator.validate(100, {"type": "integer", "maximum": 50})
        assert errs and "maximum" in errs[0]


class TestValidatorEnum:
    def test_value_in_enum(self):
        assert A2AValidator.validate("low", {"enum": ["low", "mid", "high"]}) == []

    def test_value_not_in_enum(self):
        errs = A2AValidator.validate("extreme", {"enum": ["low", "mid", "high"]})
        assert errs and "not in enum" in errs[0]


# ---------------------------------------------------------------------------
# ContractRegistry
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ContractRegistry:
    reg = ContractRegistry()
    reg.register(
        A2AContract(
            namespace="sales",
            name="scorer",
            methods={
                "score_lead": A2AMethod(
                    name="score_lead",
                    input_schema={
                        "type": "object",
                        "required": ["lead"],
                        "properties": {
                            "lead": {
                                "type": "object",
                                "required": ["id", "company"],
                                "properties": {
                                    "id": {"type": "string", "minLength": 1},
                                    "company": {"type": "string"},
                                    "score": {
                                        "type": "integer",
                                        "minimum": 0,
                                        "maximum": 100,
                                    },
                                },
                            }
                        },
                    },
                )
            },
        )
    )
    return reg


class TestContractRegistry:
    def test_register_and_get(self, registry):
        got = registry.get("sales/scorer")
        assert got is not None
        assert got.method("score_lead") is not None

    def test_validate_valid_call(self, registry):
        registry.validate_call(
            callee_namespace="sales",
            callee_name="scorer",
            method="score_lead",
            args={"lead": {"id": "L-1", "company": "Acme"}},
        )  # must not raise

    def test_validate_unknown_callee(self, registry):
        with pytest.raises(MethodNotFound, match="no contract registered"):
            registry.validate_call(
                callee_namespace="legal",
                callee_name="auditor",
                method="x",
                args={},
            )

    def test_validate_unknown_method(self, registry):
        with pytest.raises(MethodNotFound, match="exposes no method"):
            registry.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="unknown_method",
                args={},
            )

    def test_validate_missing_required_field(self, registry):
        with pytest.raises(SchemaMismatch, match="missing required field 'lead'"):
            registry.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="score_lead",
                args={},
            )

    def test_validate_wrong_type_in_nested_field(self, registry):
        with pytest.raises(SchemaMismatch, match="expected string"):
            registry.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="score_lead",
                args={"lead": {"id": 42, "company": "Acme"}},
            )

    def test_validate_enum_bounds(self, registry):
        with pytest.raises(SchemaMismatch, match="maximum"):
            registry.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="score_lead",
                args={"lead": {"id": "L-1", "company": "Acme", "score": 500}},
            )

    def test_unregister(self, registry):
        assert registry.unregister("sales/scorer") is True
        with pytest.raises(MethodNotFound):
            registry.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="score_lead",
                args={"lead": {"id": "L-1", "company": "Acme"}},
            )
