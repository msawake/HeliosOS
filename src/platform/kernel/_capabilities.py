# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
"""
Capability tokens — opaque, kernel-local runtime grants.

This is the Phase 2 #2 foundation. Today A2A and data-access checks rely
on static ACLs stored on the callee's manifest (``canBeCalledBy``,
``allowed_namespaces``). That works but can't express *runtime* grants
("for this task only, caller X may delegate to Y") and can't be revoked
without redeploying the callee.

Capability tokens fix both. The kernel mints an opaque handle that
authorizes a specific ``(subject, target, verb)`` triple. At runtime,
callers present the handle and the kernel checks three things:

    1. Does the token exist? (revocation = delete the row)
    2. Has it expired?
    3. Does the (subject, target, verb) match the requested action?

If yes, the action is allowed *regardless* of ACL — tokens are a
positive authority. If no, the pipeline falls back to the ACL check
(so this is a non-destructive addition: existing callers keep working).

Scope of this module:
    * Unsigned, opaque handles — a 128-bit hex id. The plan explicitly
      defers signed JWTs / macaroons until multi-tenant-across-orgs is
      a real requirement.
    * In-memory store by default. Durable storage follows with Phase 2
      #3 (durable IPC); swapping to a ``Store[CapabilityToken]`` is a
      one-line change on the store protocol.
    * The kernel surfaces ``issue_capability`` / ``revoke_capability``
      / ``check_capability``. The A2A handler consults these before
      falling back to its existing ACL path.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mint_token_id() -> str:
    """128 bits of randomness, hex-encoded. Unsigned = kernel-local only."""
    return secrets.token_hex(16)


# ---------------------------------------------------------------------------
# Token record
# ---------------------------------------------------------------------------


@dataclass
class CapabilityToken:
    """A runtime-issued grant.

    * ``id``         — opaque handle the caller presents.
    * ``subject``    — PID the token was issued to (caller).
    * ``target``     — qualified name of the object the token authorizes
                       (e.g. ``"sales/lead-scorer"`` for an A2A target,
                       ``"secret:pagerduty.key"`` for a secret).
    * ``verb``       — what operation the token allows. Accepts wildcard
                       ``"*"`` for any verb on the target.
    * ``issued_at`` / ``expires_at``
    * ``metadata``   — arbitrary context recorded by the issuer
                       (reason, task id, issued-by).
    """

    id: str
    subject: str
    target: str
    verb: str = "*"
    issued_at: str = field(default_factory=lambda: _now().isoformat())
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return (now or _now()) >= exp

    def authorizes(self, *, subject: str, target: str, verb: str) -> bool:
        """Does this token authorize the given (subject, target, verb)?"""
        if self.subject != subject:
            return False
        if self.target != target and self.target != "*":
            return False
        if self.verb != "*" and self.verb != verb:
            return False
        return not self.is_expired()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CapabilityStore(Protocol):
    """Minimal interface for capability-token persistence."""

    def save(self, token: CapabilityToken) -> None: ...
    def load(self, token_id: str) -> CapabilityToken | None: ...
    def delete(self, token_id: str) -> bool: ...
    def list_for_subject(self, subject: str) -> list[CapabilityToken]: ...
    def list_all(self) -> list[CapabilityToken]: ...


class InMemoryCapabilityStore:
    """Default in-process store. Durable replacement lands in Phase 2 #3."""

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


class PostgresCapabilityStore:
    """Capability store backed by the ``capability_tokens`` table (migration 013).

    The crucial property the in-memory store lacks: tokens are SHARED ACROSS
    PROCESSES. In a multi-process deployment the approval HTTP request (which
    mints the token) and the worker that resumes the run (which validates it)
    are different processes — with an in-memory store the worker can't see the
    token and the resume fails ``approval token did not authorize``. A durable
    store fixes that. The table is cross-tenant (no RLS), so reads/writes use an
    admin connection."""

    def __init__(self, db) -> None:
        self._db = db

    @staticmethod
    def _hydrate(row) -> "CapabilityToken | None":
        if not row:
            return None
        md = row["metadata"]
        if isinstance(md, str):
            import json
            md = json.loads(md or "{}")

        def _iso(v):
            return v.isoformat() if hasattr(v, "isoformat") else (v or None)

        return CapabilityToken(
            id=row["id"], subject=row["subject"], target=row["target"], verb=row["verb"],
            issued_at=_iso(row["issued_at"]) or _now().isoformat(),
            expires_at=_iso(row["expires_at"]),
            metadata=md or {},
        )

    def save(self, token: CapabilityToken) -> None:
        import json
        with self._db.admin() as conn:
            conn.execute(
                """
                INSERT INTO capability_tokens (id, subject, target, verb, issued_at, expires_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    subject=EXCLUDED.subject, target=EXCLUDED.target, verb=EXCLUDED.verb,
                    issued_at=EXCLUDED.issued_at, expires_at=EXCLUDED.expires_at,
                    metadata=EXCLUDED.metadata
                """,
                (token.id, token.subject, token.target, token.verb,
                 token.issued_at, token.expires_at, json.dumps(token.metadata or {})),
            )
            conn.commit()

    def load(self, token_id: str) -> CapabilityToken | None:
        with self._db.admin() as conn:
            row = conn.execute_one("SELECT * FROM capability_tokens WHERE id=%s", (token_id,))
        return self._hydrate(row)

    def delete(self, token_id: str) -> bool:
        with self._db.admin() as conn:
            row = conn.execute_one(
                "DELETE FROM capability_tokens WHERE id=%s RETURNING id", (token_id,)
            )
            conn.commit()
        return row is not None

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        with self._db.admin() as conn:
            rows = conn.execute_many(
                "SELECT * FROM capability_tokens WHERE subject=%s", (subject,)
            )
        return [t for t in (self._hydrate(r) for r in rows) if t is not None]

    def list_all(self) -> list[CapabilityToken]:
        with self._db.admin() as conn:
            rows = conn.execute_many("SELECT * FROM capability_tokens")
        return [t for t in (self._hydrate(r) for r in rows) if t is not None]


# ---------------------------------------------------------------------------
# CapabilityManager — public issue/revoke/check API
# ---------------------------------------------------------------------------


class CapabilityManager:
    """Issues, revokes, and verifies capability tokens.

    Sits alongside ``PermissionManager``. The A2A path consults
    :meth:`authorize` first — a valid token short-circuits the ACL
    check. This preserves every existing deny path while adding runtime
    delegation.
    """

    def __init__(self, store: CapabilityStore | None = None) -> None:
        self._store: CapabilityStore = store or InMemoryCapabilityStore()

    def issue(
        self,
        *,
        subject: str,
        target: str,
        verb: str = "*",
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CapabilityToken:
        """Mint a new token. ``ttl_seconds=None`` means no expiry."""
        expires_at: str | None = None
        if ttl_seconds is not None and ttl_seconds > 0:
            expires_at = (_now() + timedelta(seconds=ttl_seconds)).isoformat()
        token = CapabilityToken(
            id=_mint_token_id(),
            subject=subject,
            target=target,
            verb=verb,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        self._store.save(token)
        logger.debug(
            "capability issued id=%s subject=%s target=%s verb=%s ttl=%s",
            token.id, subject, target, verb, ttl_seconds,
        )
        return token

    def revoke(self, token_id: str) -> bool:
        """Delete the token. Returns True if it existed.

        Revocation is the entire security model — once the record is
        gone, subsequent ``authorize`` calls deny. No cryptographic
        attenuation chains to unwind.
        """
        deleted = self._store.delete(token_id)
        if deleted:
            logger.debug("capability revoked id=%s", token_id)
        return deleted

    def authorize(
        self,
        *,
        token_id: str,
        subject: str,
        target: str,
        verb: str,
    ) -> bool:
        """Return True iff the token exists, is unexpired, and matches."""
        token = self._store.load(token_id)
        if token is None:
            return False
        if token.is_expired():
            # Drop the stale record so list_for_subject doesn't surface it.
            self._store.delete(token_id)
            return False
        return token.authorizes(subject=subject, target=target, verb=verb)

    def get(self, token_id: str) -> CapabilityToken | None:
        return self._store.load(token_id)

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        """All non-expired tokens issued to ``subject`` (expired ones purged lazily)."""
        out = []
        for t in self._store.list_for_subject(subject):
            if t.is_expired():
                self._store.delete(t.id)
                continue
            out.append(t)
        return out


__all__ = [
    "CapabilityManager",
    "CapabilityStore",
    "CapabilityToken",
    "InMemoryCapabilityStore",
    "PostgresCapabilityStore",
]
