"""
PostgreSQL-backed stores for clients and per-client MCP configurations.

Mirrors the style of src/platform/persistence.py — all writes go through
the tenant-scoped connection so Row-Level Security policies are enforced.

Falls back to in-memory storage when no database client is connected
(e.g., tests and dev mode).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


_TRANSPORTS = ("stdio", "streamable-http")


def _coerce_tool_list(value, *, none_ok: bool = False) -> list[str] | None:
    """Normalize a JSONB tool-name column to a list[str] (or None).

    psycopg may hand back a decoded ``list`` or a raw JSON ``str`` depending on
    adapters. ``none_ok`` preserves NULL as None (allow-all semantics for
    ``allowed_tools``); otherwise NULL collapses to None and the caller applies
    its own ``or []`` default.
    """
    if value is None:
        return None if none_ok else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, list):
        return [str(v) for v in value]
    return None


def _validate_transport_shape(transport: str, package: str, url: str | None) -> None:
    """Enforce transport-shape invariants above the DB CHECK constraint.

    stdio → package required, url must be empty. streamable-http → url required
    (with an http/https scheme). Raises ValueError on shape mismatch so callers
    surface a clean 400 instead of relying on a raw psql CHECK failure.

    HTTP+SSE (the pre-2025-03-26 MCP HTTP transport) is intentionally not
    accepted — the MCP spec superseded it with Streamable HTTP.
    """
    if transport not in _TRANSPORTS:
        raise ValueError(
            f"transport must be one of {_TRANSPORTS!r}, got {transport!r}"
        )
    if transport == "stdio":
        if not package:
            raise ValueError("stdio transport requires a package")
        if url:
            raise ValueError("stdio transport must not carry a url")
    else:  # streamable-http
        if not url:
            raise ValueError("streamable-http transport requires a url")
        scheme = (url.split(":", 1)[0] or "").lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"streamable-http url scheme must be http or https, got {url!r}"
            )
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresClientStore:
    """Persistent client store backed by the `clients` table."""

    def __init__(self, db_client, tenant_id: str = "default"):
        self._db = db_client
        self._tenant_id = tenant_id
        self._memory: dict[str, dict] = {}

    @property
    def _has_db(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    def create(self, client_id: str, name: str, config: dict | None = None) -> dict:
        """Insert a new client. Raises ValueError if the id already exists."""
        record = {
            "id": client_id,
            "name": name,
            "status": "active",
            "config": config or {},
            "created_at": _now_iso(),
        }

        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    # Check for duplicate
                    existing = conn.execute_one(
                        "SELECT id FROM clients WHERE id = %s AND tenant_id = %s",
                        (client_id, self._tenant_id),
                    )
                    if existing:
                        raise ValueError(f"Client '{client_id}' already exists")
                    conn.execute(
                        "INSERT INTO clients (id, tenant_id, name, status, config) "
                        "VALUES (%s, %s, %s, 'active', %s::jsonb)",
                        (client_id, self._tenant_id, name, json.dumps(config or {})),
                    )
                    conn.commit()
                self._memory[client_id] = record
                return record
            except ValueError:
                raise
            except Exception as e:
                logger.warning("Failed to persist client %s to DB: %s", client_id, e)

        # In-memory fallback
        if client_id in self._memory:
            raise ValueError(f"Client '{client_id}' already exists")
        self._memory[client_id] = record
        return record

    def get(self, client_id: str) -> dict | None:
        """Return a client record or None if missing."""
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    row = conn.execute_one(
                        "SELECT id, name, status, config, created_at FROM clients "
                        "WHERE id = %s AND tenant_id = %s",
                        (client_id, self._tenant_id),
                    )
                    if row:
                        return self._row_to_dict(row)
            except Exception as e:
                logger.warning("Failed to fetch client %s: %s", client_id, e)
        return self._memory.get(client_id)

    def list_all(self) -> list[dict]:
        """Return all clients for this tenant."""
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rows = conn.execute(
                        "SELECT id, name, status, config, created_at FROM clients "
                        "WHERE tenant_id = %s ORDER BY created_at DESC",
                        (self._tenant_id,),
                    )
                    return [self._row_to_dict(r) for r in (rows or [])]
            except Exception as e:
                logger.warning("Failed to list clients: %s", e)
        return list(self._memory.values())

    def archive(self, client_id: str) -> bool:
        """Mark a client as archived. Returns True if the client existed."""
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rc = conn.execute(
                        "UPDATE clients SET status = 'archived', updated_at = NOW() "
                        "WHERE id = %s AND tenant_id = %s",
                        (client_id, self._tenant_id),
                    )
                    conn.commit()
                    if client_id in self._memory:
                        self._memory[client_id]["status"] = "archived"
                    return bool(rc)
            except Exception as e:
                logger.warning("Failed to archive client %s: %s", client_id, e)

        if client_id in self._memory:
            self._memory[client_id]["status"] = "archived"
            return True
        return False

    def exists(self, client_id: str) -> bool:
        return self.get(client_id) is not None

    def _row_to_dict(self, row: dict) -> dict:
        created_at = row.get("created_at")
        return {
            "id": row["id"],
            "name": row["name"],
            "status": row.get("status", "active"),
            "config": row.get("config") if isinstance(row.get("config"), dict) else (
                json.loads(row["config"]) if row.get("config") else {}
            ),
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
        }


class PostgresClientMCPStore:
    """Persistent per-client MCP server configurations."""

    def __init__(self, db_client, tenant_id: str = "default"):
        self._db = db_client
        self._tenant_id = tenant_id
        self._memory: dict[str, list[dict]] = {}

    @property
    def _has_db(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    def add(
        self,
        client_id: str,
        server_name: str,
        package: str,
        env_vars: dict | None = None,
        args: list[str] | None = None,
        *,
        transport: str = "stdio",
        url: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
    ) -> dict:
        """Add an MCP server config. Raises ValueError if a duplicate exists.

        ``transport``: one of ``'stdio'`` (default), ``'streamable-http'``, ``'sse'``.
        ``url``: required for HTTP transports, must be None for stdio (the CHECK
        constraint enforces this at the DB layer too).
        ``allowed_tools``: bare upstream tool names to expose (None = allow all).
        ``disallowed_tools``: bare upstream tool names to hide (subtracted after
        the allow-list).
        """
        _validate_transport_shape(transport, package, url)
        # Normalize the stored URL (empty string → NULL) so the CHECK constraint
        # stays happy for stdio and the DB never contains ambiguous empties.
        url = url or None
        config = {
            "server_name": server_name,
            "package": package,
            "env_vars": env_vars or {},
            "args": args or [],
            "enabled": True,
            "transport": transport,
            "url": url,
            "allowed_tools": allowed_tools,
            "disallowed_tools": disallowed_tools or [],
        }
        _allowed_json = json.dumps(allowed_tools) if allowed_tools is not None else None
        _disallowed_json = json.dumps(disallowed_tools) if disallowed_tools else None

        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    existing = conn.execute_one(
                        "SELECT id FROM client_mcp_configs "
                        "WHERE client_id = %s AND server_name = %s AND tenant_id = %s",
                        (client_id, server_name, self._tenant_id),
                    )
                    if existing:
                        raise ValueError(
                            f"Server '{server_name}' already configured for client '{client_id}'"
                        )
                    conn.execute(
                        "INSERT INTO client_mcp_configs "
                        "(tenant_id, client_id, server_name, package, env_vars, args, "
                        "enabled, transport, url, allowed_tools, disallowed_tools) "
                        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, true, %s, %s, "
                        "%s::jsonb, %s::jsonb)",
                        (self._tenant_id, client_id, server_name, package,
                         json.dumps(env_vars or {}), args or [],
                         transport, url, _allowed_json, _disallowed_json),
                    )
                    conn.commit()
                self._memory.setdefault(client_id, []).append(config)
                return config
            except ValueError:
                raise
            except Exception as e:
                logger.warning("Failed to persist MCP config %s/%s: %s", client_id, server_name, e)

        # In-memory fallback
        configs = self._memory.setdefault(client_id, [])
        for c in configs:
            if c["server_name"] == server_name:
                raise ValueError(
                    f"Server '{server_name}' already configured for client '{client_id}'"
                )
        configs.append(config)
        return config

    def list_for_client(self, client_id: str, redact_secrets: bool = False) -> list[dict]:
        """Return all MCP configs for a client. Optionally redact env_vars."""
        configs: list[dict] = []
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rows = conn.execute(
                        "SELECT server_name, package, env_vars, args, enabled, "
                        "transport, url, allowed_tools, disallowed_tools "
                        "FROM client_mcp_configs "
                        "WHERE client_id = %s AND tenant_id = %s ORDER BY server_name",
                        (client_id, self._tenant_id),
                    )
                    configs = [
                        {
                            "server_name": r["server_name"],
                            "package": r["package"],
                            "env_vars": r["env_vars"] if isinstance(r["env_vars"], dict) else (
                                json.loads(r["env_vars"]) if r["env_vars"] else {}
                            ),
                            "args": r.get("args") or [],
                            "enabled": bool(r.get("enabled", True)),
                            "transport": r.get("transport") or "stdio",
                            "url": r.get("url"),
                            "allowed_tools": _coerce_tool_list(r.get("allowed_tools"), none_ok=True),
                            "disallowed_tools": _coerce_tool_list(r.get("disallowed_tools")) or [],
                        }
                        for r in (rows or [])
                    ]
            except Exception as e:
                logger.warning("Failed to list MCP configs for %s: %s", client_id, e)
                configs = list(self._memory.get(client_id, []))
        else:
            configs = list(self._memory.get(client_id, []))

        if redact_secrets:
            configs = [
                {**c, "env_vars": {k: "***" for k in c.get("env_vars", {})}}
                for c in configs
            ]
        return configs

    def get(self, client_id: str, server_name: str) -> dict | None:
        """Return a single server config or None."""
        for cfg in self.list_for_client(client_id):
            if cfg["server_name"] == server_name:
                return cfg
        return None

    def update(
        self,
        client_id: str,
        server_name: str,
        package: str,
        env_vars: dict | None = None,
        args: list[str] | None = None,
        *,
        transport: str = "stdio",
        url: str | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
    ) -> dict | None:
        """Update an existing MCP config. Returns the new config or None if not found."""
        _validate_transport_shape(transport, package, url)
        url = url or None
        new_config = {
            "server_name": server_name,
            "package": package,
            "env_vars": env_vars or {},
            "args": args or [],
            "enabled": True,
            "transport": transport,
            "url": url,
            "allowed_tools": allowed_tools,
            "disallowed_tools": disallowed_tools or [],
        }
        _allowed_json = json.dumps(allowed_tools) if allowed_tools is not None else None
        _disallowed_json = json.dumps(disallowed_tools) if disallowed_tools else None

        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rc = conn.execute(
                        "UPDATE client_mcp_configs SET "
                        "package = %s, env_vars = %s::jsonb, args = %s, "
                        "transport = %s, url = %s, "
                        "allowed_tools = %s::jsonb, disallowed_tools = %s::jsonb, "
                        "updated_at = NOW() "
                        "WHERE client_id = %s AND server_name = %s AND tenant_id = %s",
                        (package, json.dumps(env_vars or {}), args or [],
                         transport, url, _allowed_json, _disallowed_json,
                         client_id, server_name, self._tenant_id),
                    )
                    conn.commit()
                    if not rc:
                        return None
            except Exception as e:
                logger.warning("Failed to update MCP config %s/%s: %s", client_id, server_name, e)

        # In-memory mirror
        configs = self._memory.get(client_id, [])
        for i, c in enumerate(configs):
            if c["server_name"] == server_name:
                configs[i] = new_config
                return new_config
        # If not in memory but was in DB, still return new_config
        return new_config if self._has_db else None

    def delete(self, client_id: str, server_name: str) -> bool:
        """Remove an MCP config. Returns True if it existed."""
        removed = False
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rc = conn.execute(
                        "DELETE FROM client_mcp_configs "
                        "WHERE client_id = %s AND server_name = %s AND tenant_id = %s",
                        (client_id, server_name, self._tenant_id),
                    )
                    conn.commit()
                    removed = bool(rc)
            except Exception as e:
                logger.warning("Failed to delete MCP config %s/%s: %s", client_id, server_name, e)

        configs = self._memory.get(client_id, [])
        for i, c in enumerate(configs):
            if c["server_name"] == server_name:
                configs.pop(i)
                removed = True
                break
        return removed

    def count_for_client(self, client_id: str) -> int:
        return len(self.list_for_client(client_id))
