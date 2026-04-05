"""
Google Sheets Data Connector.

Reads data from a Google Sheet and maps columns to ontology properties.
Currently implemented as a simulated connector that returns sample data,
since MCP google-workspace integration may not be connected.

When MCP is available, this connector will use the google-workspace
read_sheet_values tool to fetch real sheet data.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.intelligence.connectors.base import SyncResult
from src.intelligence.ontology import ObjectInstance

logger = logging.getLogger(__name__)


# Sample data used when MCP is not connected (simulation mode)
_SAMPLE_SHEET_DATA: list[dict[str, str]] = [
    {"company_name": "Acme Corp", "industry": "Technology", "arr": "5000000", "employees": "250", "stage": "active"},
    {"company_name": "GlobalTech", "industry": "Finance", "arr": "3200000", "employees": "180", "stage": "active"},
    {"company_name": "DataFlow Inc", "industry": "Technology", "arr": "1800000", "employees": "75", "stage": "active"},
    {"company_name": "CloudBase", "industry": "SaaS", "arr": "950000", "employees": "45", "stage": "prospect"},
    {"company_name": "NexaTech", "industry": "Healthcare", "arr": "2100000", "employees": "120", "stage": "active"},
]


class GoogleSheetsConnector:
    """Reads Google Sheets data and ingests rows as ontology objects.

    Currently operates in simulation mode with sample data.
    """

    def __init__(
        self,
        sheet_id: str = "",
        sheet_name: str = "Sheet1",
        object_type: str = "Customer",
        tenant_id: str = "default",
        simulated: bool = True,
    ) -> None:
        self.name: str = "google_sheets"
        self.supported_types: list[str] = [object_type]
        self._sheet_id = sheet_id
        self._sheet_name = sheet_name
        self._object_type = object_type
        self._tenant_id = tenant_id
        self._simulated = simulated
        self._connected = False
        self._data: list[dict[str, str]] = []

    async def connect(self, credentials: dict[str, Any] | None = None) -> bool:
        """Connect to Google Sheets (or load simulation data)."""
        if self._simulated:
            self._data = list(_SAMPLE_SHEET_DATA)
            self._connected = True
            logger.info(
                "GoogleSheetsConnector connected (simulated, %d rows)",
                len(self._data),
            )
            return True

        # Real MCP connection would go here:
        # result = await mcp_client.call_tool("read_sheet_values", {
        #     "spreadsheet_id": self._sheet_id,
        #     "range": self._sheet_name,
        # })
        # self._data = parse_sheet_response(result)
        logger.warning(
            "GoogleSheetsConnector: real MCP connection not implemented, "
            "use simulated=True"
        )
        self._connected = False
        return False

    async def discover_schema(self) -> dict[str, Any]:
        """Discover available columns in the sheet."""
        if not self._connected or not self._data:
            return {"columns": [], "row_count": 0}

        columns = list(self._data[0].keys()) if self._data else []
        return {
            "sheet_id": self._sheet_id,
            "sheet_name": self._sheet_name,
            "columns": columns,
            "row_count": len(self._data),
            "simulated": self._simulated,
        }

    async def sync(
        self,
        ontology: Any,
        type_mapping: dict[str, str],
        since: str | None = None,
    ) -> SyncResult:
        """Sync sheet data into the ontology.

        Parameters
        ----------
        ontology : InMemoryOntology
            Target ontology.
        type_mapping : dict[str, str]
            Maps sheet column names to ontology property names.
        since : str, optional
            Ignored for sheets (full sync each time).
        """
        start = time.monotonic()
        result = SyncResult()

        if not self._connected:
            result.errors.append("Not connected — call connect() first")
            result.duration_seconds = time.monotonic() - start
            return result

        for row_num, row in enumerate(self._data, start=1):
            try:
                properties = _map_row(row, type_mapping)
                obj = ObjectInstance(
                    type_name=self._object_type,
                    tenant_id=self._tenant_id,
                    properties=properties,
                    source=f"sheets:{self._sheet_id or 'simulated'}",
                )
                ontology.upsert_object(obj)
                result.objects_created += 1
            except Exception as e:
                result.errors.append(f"row {row_num}: {e}")

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "GoogleSheetsConnector sync complete: %d created, %d errors in %.2fs",
            result.objects_created,
            len(result.errors),
            result.duration_seconds,
        )
        return result

    async def disconnect(self) -> None:
        """Disconnect from the data source."""
        self._connected = False
        self._data = []


def _map_row(row: dict[str, str], type_mapping: dict[str, str]) -> dict[str, Any]:
    """Map a sheet row to ontology properties via the type mapping."""
    properties: dict[str, Any] = {}
    for sheet_col, onto_prop in type_mapping.items():
        raw = row.get(sheet_col, "")
        if raw == "":
            continue
        properties[onto_prop] = _coerce_value(raw)
    return properties


def _coerce_value(raw: str) -> Any:
    """Try to coerce a string value to int or float, otherwise return as-is."""
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
