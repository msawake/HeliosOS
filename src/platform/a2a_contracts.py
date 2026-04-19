"""
Typed agent-to-agent contracts.

Phase 2 #4. Today A2A calls are untyped string dispatch:
``agent__call(namespace, name, task)`` — the caller hopes the callee
understands its task format. LeadForge pins this with a hardcoded
``SUBAGENT_MAP`` in ``src/companies/leadforge/agent_configs.py:604``,
which breaks the moment a new consumer shows up.

This module introduces a first-class contract surface: each agent
declares one or more methods with input/output schemas on its manifest,
and the kernel validates every call against that contract before the
target runs.

What lives here:
  * ``A2AMethod`` — a single typed surface (name, input schema, output
    schema, idempotency hints, timeout hint).
  * ``A2AContract`` — all methods an agent publishes.
  * ``A2AValidator`` — small JSON-schema-subset validator. Accepts
    ``type`` (string/integer/number/boolean/array/object/null), ``required``,
    ``properties``, ``items``, ``enum``, ``minimum``/``maximum``. Enough to
    cover real agent interfaces without pulling in ``jsonschema``.
  * ``ContractRegistry`` — in-process map from qualified name to contract.
    Kernel registers contracts at admission time; A2A handler queries it.

What does NOT live here (yet):
  * The full ``jsonschema`` feature set (refs, format, oneOf). Add when
    a real use case demands it.
  * Rewriting the ``agent__call`` tool signature. That work lives in the
    A2A handler migration; this module just provides the primitives.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


@dataclass
class A2AMethod:
    """One typed method an agent exposes to A2A callers."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    idempotent: bool = False
    timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2AMethod":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=dict(data.get("input_schema") or {}),
            output_schema=dict(data.get("output_schema") or {}),
            idempotent=bool(data.get("idempotent", False)),
            timeout_seconds=data.get("timeout_seconds"),
        )


@dataclass
class A2AContract:
    """All typed methods an agent publishes to A2A."""

    namespace: str
    name: str
    methods: dict[str, A2AMethod] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}"

    def method(self, name: str) -> A2AMethod | None:
        return self.methods.get(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "name": self.name,
            "methods": {n: m.to_dict() for n, m in self.methods.items()},
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "A2AContract | None":
        """Extract a contract from a canonical manifest dict.

        Looks at ``spec.capabilities.a2a.methods`` (new) and returns
        ``None`` when no typed surface is declared. Callers that want to
        require a contract can check for ``None`` and fail-closed.
        """
        metadata = manifest.get("metadata") or {}
        spec = manifest.get("spec") or {}
        capabilities = spec.get("capabilities") or {}
        a2a = capabilities.get("a2a") or {}
        methods_raw = a2a.get("methods")
        if not methods_raw:
            return None
        methods: dict[str, A2AMethod] = {}
        for entry in methods_raw:
            m = A2AMethod.from_dict(entry)
            methods[m.name] = m
        return cls(
            namespace=metadata.get("namespace", "default"),
            name=metadata.get("name", ""),
            methods=methods,
        )


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class A2AContractError(ValueError):
    """Base for contract validation failures."""


class MethodNotFound(A2AContractError):
    """Raised when the callee has no such method."""


class SchemaMismatch(A2AContractError):
    """Raised when args don't match the method's input schema."""


# ---------------------------------------------------------------------------
# Minimal JSON-schema subset validator
# ---------------------------------------------------------------------------


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
    "null": (type(None),),
}


class A2AValidator:
    """Small structural validator (subset of JSON Schema).

    Supports: ``type``, ``required``, ``properties``, ``items``, ``enum``,
    ``minimum`` / ``maximum`` on numbers, ``minLength`` / ``maxLength`` on
    strings. Missing schema fields mean "no constraint".
    """

    @classmethod
    def validate(cls, value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
        """Return a list of human-readable errors. Empty list means OK."""
        errors: list[str] = []
        if not schema:
            return errors

        expected = schema.get("type")
        if expected is not None:
            types = _TYPE_MAP.get(expected)
            if types is None:
                errors.append(f"{path}: schema type {expected!r} is not supported")
            else:
                # bool is a subclass of int — disambiguate.
                if expected == "integer" and isinstance(value, bool):
                    errors.append(f"{path}: expected integer, got boolean")
                elif expected == "boolean" and not isinstance(value, bool):
                    errors.append(f"{path}: expected boolean")
                elif not isinstance(value, types):
                    errors.append(
                        f"{path}: expected {expected}, got {type(value).__name__}"
                    )
                    return errors

        enum = schema.get("enum")
        if enum is not None and value not in enum:
            errors.append(f"{path}: value {value!r} not in enum {list(enum)!r}")

        if isinstance(value, str):
            min_len = schema.get("minLength")
            max_len = schema.get("maxLength")
            if min_len is not None and len(value) < min_len:
                errors.append(f"{path}: length {len(value)} < minLength {min_len}")
            if max_len is not None and len(value) > max_len:
                errors.append(f"{path}: length {len(value)} > maxLength {max_len}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None and value < minimum:
                errors.append(f"{path}: value {value} < minimum {minimum}")
            if maximum is not None and value > maximum:
                errors.append(f"{path}: value {value} > maximum {maximum}")

        if isinstance(value, dict):
            required = schema.get("required") or []
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing required field {key!r}")
            for key, sub_schema in (schema.get("properties") or {}).items():
                if key in value:
                    errors.extend(cls.validate(value[key], sub_schema, path=f"{path}.{key}"))

        if isinstance(value, (list, tuple)):
            item_schema = schema.get("items")
            if item_schema:
                for idx, item in enumerate(value):
                    errors.extend(cls.validate(item, item_schema, path=f"{path}[{idx}]"))

        return errors


# ---------------------------------------------------------------------------
# ContractRegistry
# ---------------------------------------------------------------------------


class ContractRegistry:
    """Maps qualified-name to contract. Populated at admission time."""

    def __init__(self) -> None:
        self._by_qname: dict[str, A2AContract] = {}

    def register(self, contract: A2AContract) -> None:
        self._by_qname[contract.qualified_name] = contract

    def unregister(self, qualified_name: str) -> bool:
        return self._by_qname.pop(qualified_name, None) is not None

    def get(self, qualified_name: str) -> A2AContract | None:
        return self._by_qname.get(qualified_name)

    def list_all(self) -> list[A2AContract]:
        return list(self._by_qname.values())

    def validate_call(
        self,
        *,
        callee_namespace: str,
        callee_name: str,
        method: str,
        args: dict[str, Any],
    ) -> None:
        """Validate a call against a registered contract.

        Raises ``MethodNotFound`` if the callee or method is unknown,
        ``SchemaMismatch`` if the args don't satisfy the method's input
        schema. Callers that want permissive behavior when no contract
        has been registered should check ``get(qname) is None`` first.
        """
        qname = f"{callee_namespace}/{callee_name}"
        contract = self._by_qname.get(qname)
        if contract is None:
            raise MethodNotFound(f"no contract registered for {qname!r}")
        m = contract.method(method)
        if m is None:
            raise MethodNotFound(
                f"{qname} exposes no method {method!r} "
                f"(declared: {sorted(contract.methods)!r})"
            )
        errors = A2AValidator.validate(args, m.input_schema)
        if errors:
            raise SchemaMismatch(
                f"{qname}.{method}: input validation failed:\n  - "
                + "\n  - ".join(errors)
            )


__all__ = [
    "A2AContract",
    "A2AContractError",
    "A2AMethod",
    "A2AValidator",
    "ContractRegistry",
    "MethodNotFound",
    "SchemaMismatch",
]
