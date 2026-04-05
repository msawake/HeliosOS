"""
Database Data Connector.

Executes a read-only SQL query against a PostgreSQL database and maps
result columns to ontology properties. Uses the existing DatabaseClient
patterns from src/core/database.py.

When no real database connection is available, operates in simulation
mode with configurable sample data.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.intelligence.connectors.base import SyncResult
from src.intelligence.ontology import ObjectInstance

logger = logging.getLogger(__name__)


class DatabaseConnector:
    """Executes a read-only SQL query and ingests results as ontology objects.

    Parameters
    ----------
    query : str
        The SQL SELECT query to execute. Must be read-only.
    object_type : str
        The ontology type name for ingested objects.
    tenant_id : str
        Tenant ID for created objects.
    db_client : Any, optional
        A DatabaseClient instance (from src.core.database). If None,
        the connector operates in simulation mode.
    simulated_data : list[dict], optional
        Sample data to return instead of querying a real database.
    """

    def __init__(
        self,
        query: str = "",
        object_type: str = "Record",
        tenant_id: str = "default",
        db_client: Any | None = None,
        simulated_data: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name: str = "database"
        self.supported_types: list[str] = [object_type]
        self._query = query
        self._object_type = object_type
        self._tenant_id = tenant_id
        self._db_client = db_client
        self._simulated_data = simulated_data
        self._connected = False

    async def connect(self, credentials: dict[str, Any] | None = None) -> bool:
        """Validate that a database connection or simulated data is available."""
        if self._simulated_data is not None:
            self._connected = True
            logger.info(
                "DatabaseConnector connected (simulated, %d rows)",
                len(self._simulated_data),
            )
            return True

        if self._db_client is not None and getattr(self._db_client, "is_connected", False):
            self._connected = True
            logger.info("DatabaseConnector connected to database")
            return True

        logger.warning("DatabaseConnector: no database connection available")
        self._connected = False
        return False

    async def discover_schema(self) -> dict[str, Any]:
        """Describe the query and its expected columns."""
        schema: dict[str, Any] = {
            "query": self._query,
            "object_type": self._object_type,
            "simulated": self._simulated_data is not None,
        }

        if self._simulated_data and len(self._simulated_data) > 0:
            schema["columns"] = list(self._simulated_data[0].keys())
            schema["row_count"] = len(self._simulated_data)

        return schema

    async def sync(
        self,
        ontology: Any,
        type_mapping: dict[str, str],
        since: str | None = None,
    ) -> SyncResult:
        """Execute the SQL query and create ontology objects.

        Parameters
        ----------
        ontology : InMemoryOntology
            Target ontology.
        type_mapping : dict[str, str]
            Maps SQL column names to ontology property names.
        since : str, optional
            Not used for DB connector (modify the query directly for
            incremental sync).
        """
        start = time.monotonic()
        result = SyncResult()

        if not self._connected:
            result.errors.append("Not connected — call connect() first")
            result.duration_seconds = time.monotonic() - start
            return result

        # Get data
        try:
            rows = self._execute_query()
        except Exception as e:
            result.errors.append(f"Query failed: {e}")
            result.duration_seconds = time.monotonic() - start
            return result

        # Map and ingest
        for idx, row in enumerate(rows):
            try:
                properties = _map_row(row, type_mapping)
                obj = ObjectInstance(
                    type_name=self._object_type,
                    tenant_id=self._tenant_id,
                    properties=properties,
                    source=f"db:{self._object_type}",
                )
                ontology.upsert_object(obj)
                result.objects_created += 1
            except Exception as e:
                result.errors.append(f"row {idx}: {e}")

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "DatabaseConnector sync complete: %d created, %d errors in %.2fs",
            result.objects_created,
            len(result.errors),
            result.duration_seconds,
        )
        return result

    def _execute_query(self) -> list[dict[str, Any]]:
        """Execute the SQL query or return simulated data."""
        if self._simulated_data is not None:
            return list(self._simulated_data)

        if not self._db_client:
            raise RuntimeError("No database client available")

        # Use the existing DatabaseClient pattern
        with self._db_client.tenant(self._tenant_id) as conn:
            rows = conn.execute(self._query)
            # Rows from psycopg with dict_row are already dicts
            if isinstance(rows, list):
                return rows
            return []

    async def disconnect(self) -> None:
        """Release connection (actual pool management is handled by DatabaseClient)."""
        self._connected = False


def _map_row(row: dict[str, Any], type_mapping: dict[str, str]) -> dict[str, Any]:
    """Map database row columns to ontology properties via the type mapping."""
    properties: dict[str, Any] = {}
    for db_col, onto_prop in type_mapping.items():
        value = row.get(db_col)
        if value is not None:
            properties[onto_prop] = value
    return properties
