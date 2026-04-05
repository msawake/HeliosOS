"""
CSV Data Connector.

Reads CSV files from a directory, maps columns to ontology properties,
and creates ObjectInstance records. Supports incremental sync by tracking
file content hashes to skip already-imported files.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from src.intelligence.connectors.base import SyncResult
from src.intelligence.ontology import ObjectInstance

logger = logging.getLogger(__name__)


class CSVConnector:
    """Reads CSV files and ingests rows as ontology objects."""

    def __init__(
        self,
        directory: str | Path,
        object_type: str = "Record",
        tenant_id: str = "default",
    ) -> None:
        self.name: str = "csv"
        self.supported_types: list[str] = [object_type]
        self._directory = Path(directory)
        self._object_type = object_type
        self._tenant_id = tenant_id
        self._connected = False
        # Track file hashes for incremental sync
        self._synced_hashes: set[str] = set()

    async def connect(self, credentials: dict[str, Any] | None = None) -> bool:
        """Verify the directory exists and is readable."""
        if self._directory.exists() and self._directory.is_dir():
            self._connected = True
            logger.info("CSVConnector connected to %s", self._directory)
            return True
        logger.warning("CSVConnector: directory not found: %s", self._directory)
        self._connected = False
        return False

    async def discover_schema(self) -> dict[str, Any]:
        """Discover CSV files and their column headers."""
        schema: dict[str, Any] = {"files": {}}
        if not self._connected:
            return schema

        for csv_file in sorted(self._directory.glob("*.csv")):
            try:
                with open(csv_file, newline="", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    headers = next(reader, [])
                schema["files"][csv_file.name] = {
                    "columns": headers,
                    "path": str(csv_file),
                }
            except Exception as e:
                logger.warning("Failed to read headers from %s: %s", csv_file.name, e)

        return schema

    async def sync(
        self,
        ontology: Any,
        type_mapping: dict[str, str],
        since: str | None = None,
    ) -> SyncResult:
        """Read all CSV files and create ontology objects.

        Parameters
        ----------
        ontology : InMemoryOntology
            Target ontology to ingest into.
        type_mapping : dict[str, str]
            Maps CSV column names to ontology property names.
            Example: {"Company Name": "name", "Annual Revenue": "revenue"}
        since : str, optional
            Ignored for CSV (we use file hash tracking instead).
        """
        start = time.monotonic()
        result = SyncResult()

        if not self._connected:
            result.errors.append("Not connected — call connect() first")
            result.duration_seconds = time.monotonic() - start
            return result

        csv_files = sorted(self._directory.glob("*.csv"))
        if not csv_files:
            logger.info("CSVConnector: no CSV files in %s", self._directory)
            result.duration_seconds = time.monotonic() - start
            return result

        for csv_file in csv_files:
            try:
                file_result = self._sync_file(csv_file, ontology, type_mapping)
                result.objects_created += file_result.objects_created
                result.objects_updated += file_result.objects_updated
                result.errors.extend(file_result.errors)
            except Exception as e:
                result.errors.append(f"{csv_file.name}: {e}")
                logger.error("CSVConnector: failed to sync %s: %s", csv_file.name, e)

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "CSVConnector sync complete: %d created, %d updated, %d errors in %.2fs",
            result.objects_created,
            result.objects_updated,
            len(result.errors),
            result.duration_seconds,
        )
        return result

    def _sync_file(
        self,
        csv_file: Path,
        ontology: Any,
        type_mapping: dict[str, str],
    ) -> SyncResult:
        """Sync a single CSV file into the ontology."""
        result = SyncResult()

        # Read the full file content for hashing
        raw_content = csv_file.read_bytes()
        file_hash = hashlib.sha256(raw_content).hexdigest()

        # Skip if already imported (incremental sync)
        if file_hash in self._synced_hashes:
            logger.debug("CSVConnector: skipping %s (already imported)", csv_file.name)
            return result

        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row_num, row in enumerate(reader, start=1):
                try:
                    properties = self._map_row(row, type_mapping)
                    obj = ObjectInstance(
                        type_name=self._object_type,
                        tenant_id=self._tenant_id,
                        properties=properties,
                        source=f"csv:{csv_file.name}",
                    )
                    ontology.upsert_object(obj)
                    result.objects_created += 1
                except Exception as e:
                    result.errors.append(f"{csv_file.name}:row {row_num}: {e}")

        # Mark this file hash as synced
        self._synced_hashes.add(file_hash)
        return result

    @staticmethod
    def _map_row(row: dict[str, str], type_mapping: dict[str, str]) -> dict[str, Any]:
        """Map CSV row values to ontology properties using the type_mapping.

        Attempts basic type coercion: numbers are parsed as int/float.
        """
        properties: dict[str, Any] = {}
        for csv_col, onto_prop in type_mapping.items():
            raw = row.get(csv_col, "")
            if raw == "":
                continue
            # Attempt numeric coercion
            properties[onto_prop] = _coerce_value(raw)
        return properties

    async def disconnect(self) -> None:
        """No persistent connection to close for CSV."""
        self._connected = False


def _coerce_value(raw: str) -> Any:
    """Try to coerce a string value to int or float, otherwise return as-is."""
    # Try int first
    try:
        return int(raw)
    except ValueError:
        pass
    # Try float
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
