# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""Permissive capabilities stub — all capability checks authorize."""
from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


@dataclass
class CapabilityToken:
    id: str
    subject: str
    target: str
    verb: str = "*"
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return False

    def authorizes(self, *, subject: str, target: str, verb: str) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class CapabilityStore(Protocol):
    def save(self, token: CapabilityToken) -> None: ...
    def load(self, token_id: str) -> CapabilityToken | None: ...
    def delete(self, token_id: str) -> bool: ...
    def list_for_subject(self, subject: str) -> list[CapabilityToken]: ...
    def list_all(self) -> list[CapabilityToken]: ...


class InMemoryCapabilityStore:
    def __init__(self) -> None:
        self._by_id: dict[str, CapabilityToken] = {}

    def save(self, token: CapabilityToken) -> None:
        self._by_id[token.id] = token

    def load(self, token_id: str) -> CapabilityToken | None:
        return self._by_id.get(token_id)

    def delete(self, token_id: str) -> bool:
        return self._by_id.pop(token_id, None) is not None

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        return [t for t in self._by_id.values() if t.subject == subject]

    def list_all(self) -> list[CapabilityToken]:
        return list(self._by_id.values())


class CapabilityManager:
    def __init__(self, store: CapabilityStore | None = None) -> None:
        self._store: CapabilityStore = store or InMemoryCapabilityStore()

    def issue(self, *, subject: str, target: str, verb: str = "*",
              ttl_seconds: float | None = None, metadata: dict[str, Any] | None = None) -> CapabilityToken:
        token = CapabilityToken(id=secrets.token_hex(16), subject=subject, target=target, verb=verb)
        self._store.save(token)
        return token

    def revoke(self, token_id: str) -> bool:
        return self._store.delete(token_id)

    def authorize(self, *, token_id: str, subject: str, target: str, verb: str) -> bool:
        return True

    def get(self, token_id: str) -> CapabilityToken | None:
        return self._store.load(token_id)

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        return self._store.list_for_subject(subject)
