"""
Generic REST API Data Connector.

Calls a REST API endpoint (GET), parses the JSON response, and maps
response fields to ontology properties. Configurable URL, headers,
authentication, and JSONPath-like response path to locate the array
of objects.

Currently operates in simulation mode when no real HTTP client is
available, returning sample data for testing.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from src.intelligence.connectors.base import SyncResult
from src.intelligence.ontology import ObjectInstance

logger = logging.getLogger(__name__)

# Try to import httpx/aiohttp for real HTTP requests
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class GenericAPIConnector:
    """Calls a REST API endpoint and ingests JSON data into the ontology.

    Parameters
    ----------
    url : str
        The REST endpoint URL (GET).
    headers : dict, optional
        HTTP headers (e.g., Authorization).
    auth : dict, optional
        Auth config: {"type": "bearer", "token": "..."} or
        {"type": "basic", "username": "...", "password": "..."}.
    response_path : str, optional
        Dot-separated path to the array of objects in the JSON response.
        For example, "data.results" extracts response["data"]["results"].
        If empty, the top-level response is expected to be a list.
    object_type : str
        The ontology type name for ingested objects.
    tenant_id : str
        Tenant ID for created objects.
    simulated_data : list[dict], optional
        If provided, use this data instead of making HTTP requests.
        Useful for testing.
    """

    def __init__(
        self,
        url: str = "",
        headers: dict[str, str] | None = None,
        auth: dict[str, str] | None = None,
        response_path: str = "",
        object_type: str = "Record",
        tenant_id: str = "default",
        simulated_data: list[dict[str, Any]] | None = None,
    ) -> None:
        self.name: str = "api"
        self.supported_types: list[str] = [object_type]
        self._url = url
        self._headers = headers or {}
        self._auth = auth
        self._response_path = response_path
        self._object_type = object_type
        self._tenant_id = tenant_id
        self._simulated_data = simulated_data
        self._connected = False
        self._fetched_data: list[dict[str, Any]] = []

    async def connect(self, credentials: dict[str, Any] | None = None) -> bool:
        """Validate configuration and optionally test the endpoint."""
        if self._simulated_data is not None:
            self._connected = True
            logger.info("GenericAPIConnector connected (simulated, %d records)", len(self._simulated_data))
            return True

        if not self._url:
            logger.warning("GenericAPIConnector: no URL configured")
            return False

        # Apply credentials if provided
        if credentials:
            if "token" in credentials:
                self._headers["Authorization"] = f"Bearer {credentials['token']}"
            if "api_key" in credentials:
                self._headers["X-API-Key"] = credentials["api_key"]

        self._connected = True
        logger.info("GenericAPIConnector connected to %s", self._url)
        return True

    async def discover_schema(self) -> dict[str, Any]:
        """Describe the API endpoint configuration."""
        return {
            "url": self._url,
            "response_path": self._response_path,
            "object_type": self._object_type,
            "has_auth": bool(self._auth or self._headers.get("Authorization")),
            "simulated": self._simulated_data is not None,
        }

    async def sync(
        self,
        ontology: Any,
        type_mapping: dict[str, str],
        since: str | None = None,
    ) -> SyncResult:
        """Fetch data from the API and create ontology objects.

        Parameters
        ----------
        ontology : InMemoryOntology
            Target ontology.
        type_mapping : dict[str, str]
            Maps API response field names to ontology property names.
        since : str, optional
            If the API supports incremental queries, this timestamp can be
            appended as a query parameter. Currently not implemented.
        """
        start = time.monotonic()
        result = SyncResult()

        if not self._connected:
            result.errors.append("Not connected — call connect() first")
            result.duration_seconds = time.monotonic() - start
            return result

        # Fetch the data
        try:
            records = await self._fetch_data()
        except Exception as e:
            result.errors.append(f"Fetch failed: {e}")
            result.duration_seconds = time.monotonic() - start
            return result

        # Map and ingest
        for idx, record in enumerate(records):
            try:
                properties = _map_record(record, type_mapping)
                obj = ObjectInstance(
                    type_name=self._object_type,
                    tenant_id=self._tenant_id,
                    properties=properties,
                    source=f"api:{self._url or 'simulated'}",
                )
                ontology.upsert_object(obj)
                result.objects_created += 1
            except Exception as e:
                result.errors.append(f"record {idx}: {e}")

        result.duration_seconds = time.monotonic() - start
        logger.info(
            "GenericAPIConnector sync complete: %d created, %d errors in %.2fs",
            result.objects_created,
            len(result.errors),
            result.duration_seconds,
        )
        return result

    async def _fetch_data(self) -> list[dict[str, Any]]:
        """Fetch data from the API or return simulated data."""
        if self._simulated_data is not None:
            return list(self._simulated_data)

        if not HAS_AIOHTTP:
            raise RuntimeError(
                "aiohttp is required for real API calls. "
                "Install it or provide simulated_data for testing."
            )

        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(self._url) as resp:
                resp.raise_for_status()
                data = await resp.json()

        # Navigate the response path
        return _extract_path(data, self._response_path)

    async def disconnect(self) -> None:
        """No persistent connection to close."""
        self._connected = False
        self._fetched_data = []


def _extract_path(data: Any, path: str) -> list[dict[str, Any]]:
    """Navigate a dot-separated path into a JSON structure.

    Example: _extract_path({"data": {"results": [...]}}, "data.results") -> [...]
    """
    if not path:
        if isinstance(data, list):
            return data
        return [data] if isinstance(data, dict) else []

    current = data
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return []
        if current is None:
            return []

    if isinstance(current, list):
        return current
    return [current] if isinstance(current, dict) else []


def _map_record(record: dict[str, Any], type_mapping: dict[str, str]) -> dict[str, Any]:
    """Map API response fields to ontology properties via the type mapping."""
    properties: dict[str, Any] = {}
    for api_field, onto_prop in type_mapping.items():
        value = record.get(api_field)
        if value is not None:
            properties[onto_prop] = value
    return properties
