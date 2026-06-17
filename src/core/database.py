"""
Database connection layer for Helios OS SaaS.

Provides connection pooling, tenant context management, and
Row-Level Security (RLS) enforcement for multi-tenant isolation.

In production, connects to Cloud SQL PostgreSQL via the Cloud SQL
Python Connector. In development, connects via standard DATABASE_URL.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Try psycopg (PostgreSQL driver)
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False

# Try Google Cloud SQL Connector
try:
    from google.cloud.sql.connector import Connector as CloudSQLConnector
    HAS_CLOUD_SQL = True
except ImportError:
    HAS_CLOUD_SQL = False


@dataclass
class DatabaseConfig:
    """Database connection configuration."""
    url: str = ""
    cloud_sql_instance: str = ""  # project:region:instance
    database: str = "forgeos"
    user: str = "forgeos"
    password: str = ""
    min_pool_size: int = 2
    max_pool_size: int = 10

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        """Load config from environment variables."""
        return cls(
            url=os.environ.get("DATABASE_URL", ""),
            cloud_sql_instance=os.environ.get("CLOUD_SQL_INSTANCE", ""),
            database=os.environ.get("DB_NAME", "forgeos"),
            user=os.environ.get("DB_USER", "forgeos"),
            password=os.environ.get("DB_PASSWORD", ""),
            min_pool_size=int(os.environ.get("DB_MIN_POOL", "2")),
            max_pool_size=int(os.environ.get("DB_MAX_POOL", "10")),
        )


class DatabaseClient:
    """
    Multi-tenant database client with connection pooling and RLS.

    Usage:
        db = DatabaseClient.connect(config)
        with db.tenant("tenant-123") as conn:
            rows = conn.execute("SELECT * FROM events").fetchall()
            # Only returns tenant-123's events (enforced by RLS)
    """

    def __init__(self, pool=None):
        self._pool = pool

    @classmethod
    def connect(cls, config: DatabaseConfig | None = None) -> DatabaseClient:
        """Create a database client with connection pooling."""
        if not HAS_PSYCOPG:
            logger.info("psycopg not installed — database operations will be unavailable")
            return cls(pool=None)

        cfg = config or DatabaseConfig.from_env()

        if not cfg.url and not cfg.cloud_sql_instance:
            logger.info("No DATABASE_URL or CLOUD_SQL_INSTANCE — database unavailable")
            return cls(pool=None)

        conninfo = cfg.url
        if not conninfo and cfg.cloud_sql_instance and HAS_CLOUD_SQL:
            # Build conninfo for Cloud SQL
            connector = CloudSQLConnector()
            conn = connector.connect(
                cfg.cloud_sql_instance, "pg8000",
                user=cfg.user, password=cfg.password, db=cfg.database,
            )
            conninfo = f"host={cfg.cloud_sql_instance} dbname={cfg.database} user={cfg.user}"

        try:
            pool = ConnectionPool(
                conninfo=conninfo,
                min_size=cfg.min_pool_size,
                max_size=cfg.max_pool_size,
                kwargs={"row_factory": dict_row},
            )
            logger.info("Database pool created (min=%d, max=%d)", cfg.min_pool_size, cfg.max_pool_size)
            return cls(pool=pool)
        except Exception as e:
            logger.error("Failed to create database pool: %s", e)
            return cls(pool=None)

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    @contextmanager
    def tenant(self, tenant_id: str):
        """Get a tenant-scoped database connection.

        Sets the PostgreSQL session variable `app.current_tenant` so that
        Row-Level Security policies automatically filter all queries.
        """
        if not self._pool:
            raise RuntimeError("Database not connected")

        with self._pool.connection() as conn:
            # SET doesn't support parameterized queries ($1) — use
            # set_config() which does, and is SQL-injection-safe.
            conn.execute(
                "SELECT set_config('app.current_tenant', %s, true)",
                (tenant_id,),
            )
            yield TenantConnection(conn, tenant_id)

    @contextmanager
    def admin(self):
        """Get an admin connection (no tenant scoping).

        Use for cross-tenant operations like billing, migrations, etc.
        """
        if not self._pool:
            raise RuntimeError("Database not connected")

        with self._pool.connection() as conn:
            # RESET doesn't take parameters; set_config with empty string is equivalent.
            conn.execute("SELECT set_config('app.current_tenant', '', true)")
            yield TenantConnection(conn, tenant_id=None)

    def close(self):
        if self._pool:
            self._pool.close()
            logger.info("Database pool closed")


class TenantConnection:
    """Wrapper around a psycopg connection with tenant context."""

    def __init__(self, conn, tenant_id: str | None):
        self._conn = conn
        self.tenant_id = tenant_id

    def execute(self, query: str, params: tuple | dict | None = None) -> Any:
        """Execute a query and return results."""
        cursor = self._conn.execute(query, params)
        try:
            return cursor.fetchall()
        except psycopg.ProgrammingError:
            # No results (INSERT/UPDATE/DELETE)
            return cursor.rowcount

    def execute_many(self, query: str, params: tuple | dict | None = None) -> list:
        """Execute a query and return all rows as a list of dicts."""
        cursor = self._conn.execute(query, params)
        try:
            return cursor.fetchall()
        except psycopg.ProgrammingError:
            return []

    def execute_one(self, query: str, params: tuple | dict | None = None) -> dict | None:
        """Execute a query and return a single row."""
        cursor = self._conn.execute(query, params)
        return cursor.fetchone()

    def commit(self):
        self._conn.commit()


class InMemoryDatabaseClient:
    """
    Stub database client for testing and development.
    Provides the same interface as DatabaseClient but stores nothing.
    Used when no DATABASE_URL is configured.
    """

    @property
    def is_connected(self) -> bool:
        return False

    def close(self):
        pass


def create_database_client(config: DatabaseConfig | None = None) -> DatabaseClient:
    """Factory: create the appropriate database client."""
    cfg = config or DatabaseConfig.from_env()
    if cfg.url or cfg.cloud_sql_instance:
        return DatabaseClient.connect(cfg)
    return DatabaseClient(pool=None)
