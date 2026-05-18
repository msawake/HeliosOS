"""Phase A #2 + A #3 verification — A2A handler consults capability tokens
and validates contracts at call time."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.kernel

from src.platform.a2a import A2AHandler
from src.platform.a2a_contracts import A2AContract, A2AMethod, ContractRegistry
from src.platform.capabilities import CapabilityManager
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from stacks.base import AgentDefinition, AgentResult, AgentStatus, ExecutionType, OwnershipType


class _FakeExecutor:
    """Stand-in for PlatformExecutor that returns a canned AgentResult."""

    def __init__(self, registry):
        self.registry = registry
        self.calls: list[tuple[str, str]] = []

    async def invoke(self, agent_id, prompt, context=None, **kwargs):
        self.calls.append((agent_id, prompt))
        return AgentResult(agent_id=agent_id, status=AgentStatus.COMPLETED, output="ok")


def _callee(*, namespace: str = "sales", name: str = "scorer", acl: list | None = None) -> AgentDefinition:
    agent = AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace=namespace,
    )
    if acl is not None:
        agent.metadata["_capabilities"] = {"a2a": {"canBeCalledBy": acl}}
    return agent


def _caller_context(pid: str = "caller-1", namespace: str = "outside", name: str = "requester") -> dict:
    return {
        "agent_id": pid,
        "namespace": namespace,
        "agent_name": name,
    }


async def _build(registry: AgentRegistry, **kwargs) -> A2AHandler:
    handler = A2AHandler(**kwargs)
    handler.bind_executor(_FakeExecutor(registry))
    return handler


# ---------------------------------------------------------------------------
# Phase A #2 — capability tokens in A2A
# ---------------------------------------------------------------------------


class TestCapabilityTokenShortCircuit:
    async def test_acl_denies_by_default(self):
        # No ACL + cross-namespace = default deny.
        registry = AgentRegistry()
        registry.register(_callee())
        handler = await _build(registry)
        result = await handler.call(
            caller_context=_caller_context(),
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is False
        assert "permission denied" in result["error"]

    async def test_valid_token_bypasses_acl_deny(self):
        registry = AgentRegistry()
        registry.register(_callee())
        caps = CapabilityManager()
        token = caps.issue(
            subject="caller-1", target="sales/scorer", verb="a2a.invoke"
        )
        handler = await _build(registry, capability_manager=caps)

        ctx = _caller_context()
        ctx["capability_token"] = token.id
        result = await handler.call(
            caller_context=ctx,
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is True
        assert result["output"] == "ok"

    async def test_revoked_token_falls_back_to_acl_deny(self):
        registry = AgentRegistry()
        registry.register(_callee())
        caps = CapabilityManager()
        token = caps.issue(subject="caller-1", target="sales/scorer", verb="a2a.invoke")
        caps.revoke(token.id)

        handler = await _build(registry, capability_manager=caps)
        ctx = _caller_context()
        ctx["capability_token"] = token.id
        result = await handler.call(
            caller_context=ctx,
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is False
        assert "permission denied" in result["error"]

    async def test_token_for_wrong_target_not_honored(self):
        registry = AgentRegistry()
        registry.register(_callee(namespace="sales", name="scorer"))
        registry.register(_callee(namespace="legal", name="auditor"))
        caps = CapabilityManager()
        token = caps.issue(
            subject="caller-1", target="legal/auditor", verb="a2a.invoke"
        )

        handler = await _build(registry, capability_manager=caps)
        ctx = _caller_context()
        ctx["capability_token"] = token.id
        # Token is for legal/auditor but call targets sales/scorer.
        result = await handler.call(
            caller_context=ctx,
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is False

    async def test_token_for_wrong_subject_not_honored(self):
        registry = AgentRegistry()
        registry.register(_callee())
        caps = CapabilityManager()
        # Token issued to someone else.
        token = caps.issue(
            subject="different-caller", target="sales/scorer", verb="a2a.invoke"
        )

        handler = await _build(registry, capability_manager=caps)
        ctx = _caller_context(pid="caller-1")
        ctx["capability_token"] = token.id
        result = await handler.call(
            caller_context=ctx,
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is False

    async def test_acl_allows_without_token(self):
        # Caller in same namespace — ACL default-permits, no token needed.
        registry = AgentRegistry()
        registry.register(_callee())
        handler = await _build(registry)
        ctx = _caller_context(namespace="sales", name="teammate")
        result = await handler.call(
            caller_context=ctx,
            target_namespace="sales",
            target_name="scorer",
            task="ping",
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Phase A #3 — typed contract validation at call time
# ---------------------------------------------------------------------------


class TestContractValidationAtCall:
    async def test_call_with_valid_args_passes(self):
        registry = AgentRegistry()
        registry.register(_callee())
        contracts = ContractRegistry()
        contracts.register(
            A2AContract(
                namespace="sales",
                name="scorer",
                methods={
                    "invoke": A2AMethod(
                        name="invoke",
                        input_schema={
                            "type": "object",
                            "required": ["task"],
                            "properties": {
                                "task": {"type": "string", "minLength": 1},
                                "context": {"type": "object"},
                            },
                        },
                    )
                },
            )
        )
        handler = await _build(registry, contract_registry=contracts)

        # Same-namespace caller (ACL default-permit) + valid task.
        result = await handler.call(
            caller_context=_caller_context(namespace="sales", name="teammate"),
            target_namespace="sales",
            target_name="scorer",
            task="score this lead",
        )
        assert result["success"] is True

    async def test_call_with_empty_task_violates_contract(self):
        registry = AgentRegistry()
        registry.register(_callee())
        contracts = ContractRegistry()
        contracts.register(
            A2AContract(
                namespace="sales",
                name="scorer",
                methods={
                    "invoke": A2AMethod(
                        name="invoke",
                        input_schema={
                            "type": "object",
                            "required": ["task"],
                            "properties": {"task": {"type": "string", "minLength": 1}},
                        },
                    )
                },
            )
        )
        handler = await _build(registry, contract_registry=contracts)
        result = await handler.call(
            caller_context=_caller_context(namespace="sales", name="teammate"),
            target_namespace="sales",
            target_name="scorer",
            task="",  # violates minLength
        )
        assert result["success"] is False
        assert "contract validation failed" in result["error"]

    async def test_call_without_contract_is_unvalidated(self):
        """Agents that don't declare a typed surface behave exactly as before."""
        registry = AgentRegistry()
        registry.register(_callee())
        contracts = ContractRegistry()  # empty
        handler = await _build(registry, contract_registry=contracts)
        result = await handler.call(
            caller_context=_caller_context(namespace="sales", name="teammate"),
            target_namespace="sales",
            target_name="scorer",
            task="whatever",
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Kernel binding
# ---------------------------------------------------------------------------


class TestKernelBindsA2AHandler:
    def test_kernel_binds_manager_and_contracts_on_construction(self):
        handler = A2AHandler()
        assert handler._capability_manager is None
        assert handler._contract_registry is None

        kernel = Kernel(a2a_handler=handler)
        # Kernel construction pushes its capability manager + contracts into the handler.
        assert handler._capability_manager is kernel.capabilities_mgr
        assert handler._contract_registry is kernel.contracts
