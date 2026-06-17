"""
Database migration runner.

Scans `infrastructure/database/*.sql` files in lexicographic order and
applies any that haven't been recorded in the `schema_migrations` table.
Each migration runs inside a transaction; on failure the file is rolled
back and an exception is raised.

Usage:
    from src.core.migrations import run_migrations
    run_migrations(db_client)

CLI:
    python -m src.core.migrations [--dir path/to/migrations]
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "database"

SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT
);
"""


def list_migrations(migrations_dir: Path | str | None = None) -> list[Path]:
    """Return all `*.sql` files in the migrations directory, sorted."""
    d = Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
    if not d.exists():
        return []
    files = sorted(d.glob("*.sql"))
    return files


def _applied_versions(conn) -> set[str]:
    """Return the set of already-applied migration filenames."""
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    return {r["version"] for r in (rows or [])}


def _ensure_schema_migrations_table(conn) -> None:
    conn.execute(SCHEMA_MIGRATIONS_SQL)
    conn.commit()


def _apply_migration(conn, path: Path) -> None:
    """Apply a single migration file within a transaction."""
    sql = path.read_text()
    try:
        conn.execute(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s)",
            (path.name,),
        )
        conn.commit()
        logger.info("Applied migration: %s", path.name)
    except Exception:
        try:
            conn._conn.rollback()
        except Exception:
            pass
        logger.exception("Migration failed: %s", path.name)
        raise


def run_migrations(db_client, migrations_dir: Path | str | None = None) -> dict:
    """Apply all pending migrations.

    Args:
        db_client: DatabaseClient or InMemoryDatabaseClient.
        migrations_dir: override the default `infrastructure/database/` path.

    Returns a dict with counts: `{total, applied, skipped}`.
    """
    if not getattr(db_client, "is_connected", False):
        logger.info("Migrations skipped — database not connected (in-memory mode)")
        return {"total": 0, "applied": 0, "skipped": 0, "reason": "no_db"}

    migrations = list_migrations(migrations_dir)
    if not migrations:
        logger.warning("No migration files found in %s", migrations_dir or DEFAULT_MIGRATIONS_DIR)
        return {"total": 0, "applied": 0, "skipped": 0}

    with db_client.admin() as conn:
        _ensure_schema_migrations_table(conn)
        already_applied = _applied_versions(conn)

        applied = 0
        skipped = 0
        for path in migrations:
            if path.name in already_applied:
                skipped += 1
                logger.debug("Skipping already-applied migration: %s", path.name)
                continue
            _apply_migration(conn, path)
            applied += 1

    logger.info(
        "Migrations complete: %d applied, %d skipped (of %d total)",
        applied, skipped, len(migrations),
    )
    return {"total": len(migrations), "applied": applied, "skipped": skipped}


def main() -> int:
    """CLI entry point: `python -m src.core.migrations`."""
    import argparse

    parser = argparse.ArgumentParser(description="Run Helios OS database migrations")
    parser.add_argument("--dir", help="Override migrations directory", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    )

    from src.core.database import DatabaseClient, DatabaseConfig

    config = DatabaseConfig.from_env()
    if not config.url and not config.cloud_sql_instance:
        logger.error("DATABASE_URL not set — cannot run migrations")
        return 2

    db = DatabaseClient.connect(config)
    if not db.is_connected:
        logger.error("Failed to connect to database")
        return 1

    try:
        result = run_migrations(db, migrations_dir=args.dir)
        print(f"Migrations: {result}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
