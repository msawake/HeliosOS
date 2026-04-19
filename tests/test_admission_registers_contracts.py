"""Phase A #3 verification — AdmissionController registers typed A2A contracts."""

from __future__ import annotations

import pytest

from src.platform.a2a_contracts import (
    A2AContract,
    ContractRegistry,
    MethodNotFound,
    SchemaMismatch,
)
from src.platform.kernel import AdmissionController, Kernel
from src.platform.registry import AgentRegistry


def _base_contract(
    name: str = "scorer",
    namespace: str = "sales",
    **extra_metadata,
) -> dict:
    metadata: dict = {}
    if namespace != "default":
        metadata["_namespace"] = namespace
    metadata.update(extra_metadata)
    return {
        "name": name,
        "stack": "forgeos",
        "execution_type": "reflex",
        "ownership": "shared",
        "metadata": metadata,
        "tools": [],
    }


def _contract_with_a2a_methods(**overrides) -> dict:
    base = _base_contract(**overrides)
    base["metadata"]["_capabilities"] = {
        "a2a": {
            "methods": [
                {
                    "name": "score_lead",
                    "input_schema": {
                        "type": "object",
                        "required": ["lead"],
                        "properties": {
                            "lead": {
                                "type": "object",
                                "required": ["id"],
                                "properties": {
                                    "id": {"type": "string"},
                                },
                            }
                        },
                    },
                }
            ]
        }
    }
    return base


class TestRegistrationOnAdmit:
    def test_agent_without_methods_registers_nothing(self):
        cr = ContractRegistry()
        admission = AdmissionController(
            registry=AgentRegistry(), contract_registry=cr
        )
        result = admission.admit(_base_contract())
        assert result.admitted
        assert cr.list_all() == []

    def test_agent_with_methods_registers_contract(self):
        cr = ContractRegistry()
        admission = AdmissionController(
            registry=AgentRegistry(), contract_registry=cr
        )
        result = admission.admit(_contract_with_a2a_methods())
        assert result.admitted
        assert len(cr.list_all()) == 1
        got: A2AContract = cr.list_all()[0]
        assert got.qualified_name == "sales/scorer"
        assert got.method("score_lead") is not None

    def test_failed_admission_does_not_register_contract(self):
        cr = ContractRegistry()
        admission = AdmissionController(
            registry=AgentRegistry(), contract_registry=cr
        )
        # Missing name — admission fails.
        bad = _contract_with_a2a_methods()
        bad["name"] = ""
        result = admission.admit(bad)
        assert not result.admitted
        assert cr.list_all() == []


class TestKernelFacadeExposesContracts:
    def test_kernel_builds_contract_registry(self):
        kernel = Kernel()
        assert isinstance(kernel.contracts, ContractRegistry)

    def test_admit_via_kernel_registers_contract(self):
        kernel = Kernel(registry=AgentRegistry())
        result = kernel.admission.admit(_contract_with_a2a_methods())
        assert result.admitted
        # Contract is discoverable through the kernel facade.
        contract = kernel.contracts.get("sales/scorer")
        assert contract is not None

    def test_validate_call_through_registered_contract(self):
        kernel = Kernel(registry=AgentRegistry())
        kernel.admission.admit(_contract_with_a2a_methods())

        # Valid call — no raise.
        kernel.contracts.validate_call(
            callee_namespace="sales",
            callee_name="scorer",
            method="score_lead",
            args={"lead": {"id": "L-1"}},
        )

        # Missing required field — SchemaMismatch.
        with pytest.raises(SchemaMismatch, match="missing required field 'id'"):
            kernel.contracts.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="score_lead",
                args={"lead": {}},
            )

        # Unknown method — MethodNotFound.
        with pytest.raises(MethodNotFound, match="exposes no method"):
            kernel.contracts.validate_call(
                callee_namespace="sales",
                callee_name="scorer",
                method="unknown",
                args={},
            )

    def test_agent_without_methods_has_no_validatable_contract(self):
        kernel = Kernel(registry=AgentRegistry())
        kernel.admission.admit(_base_contract(name="plain"))
        with pytest.raises(MethodNotFound, match="no contract registered"):
            kernel.contracts.validate_call(
                callee_namespace="default",
                callee_name="plain",
                method="anything",
                args={},
            )
