"""
ForgeOS Intelligence Connectors — data source adapters for ontology ingestion.

Factory function creates connectors from config dicts:

    connectors = create_connectors([
        {"type": "csv", "directory": "data/imports/", "object_type": "Customer"},
        {"type": "google_sheets", "sheet_id": "1BxiM...", "object_type": "Lead"},
        {"type": "api", "url": "https://api.example.com/leads", "object_type": "Lead"},
        {"type": "database", "query": "SELECT * FROM customers", "object_type": "Customer"},
    ])
"""

from __future__ import annotations

import logging
from typing import Any

from src.intelligence.connectors.base import DataConnector, SyncResult

logger = logging.getLogger(__name__)


def create_connectors(connector_configs: list[dict[str, Any]]) -> list[Any]:
    """Create connector instances from a list of config dicts.

    Each config dict must have a "type" key. Supported types:
    - "csv": CSVConnector
    - "google_sheets" / "sheets": GoogleSheetsConnector
    - "api": GenericAPIConnector
    - "database" / "db": DatabaseConnector

    Additional keys in the dict are passed as constructor kwargs.

    Returns
    -------
    list
        List of connector instances (each satisfies the DataConnector protocol).
    """
    connectors: list[Any] = []

    for config in connector_configs:
        connector_type = config.get("type", "").lower()
        # Copy config and remove 'type' so remaining keys are kwargs
        kwargs = {k: v for k, v in config.items() if k != "type"}

        try:
            connector = _create_one(connector_type, kwargs)
            if connector is not None:
                connectors.append(connector)
                logger.info("Created connector: %s (%s)", connector.name, connector_type)
        except Exception as e:
            logger.error("Failed to create connector type=%s: %s", connector_type, e)

    return connectors


def _create_one(connector_type: str, kwargs: dict[str, Any]) -> Any | None:
    """Instantiate a single connector by type string."""
    if connector_type == "csv":
        from src.intelligence.connectors.csv_connector import CSVConnector
        return CSVConnector(**kwargs)

    if connector_type in ("google_sheets", "sheets"):
        from src.intelligence.connectors.sheets_connector import GoogleSheetsConnector
        return GoogleSheetsConnector(**kwargs)

    if connector_type == "api":
        from src.intelligence.connectors.api_connector import GenericAPIConnector
        return GenericAPIConnector(**kwargs)

    if connector_type in ("database", "db"):
        from src.intelligence.connectors.db_connector import DatabaseConnector
        return DatabaseConnector(**kwargs)

    logger.warning("Unknown connector type: %s", connector_type)
    return None


__all__ = [
    "DataConnector",
    "SyncResult",
    "create_connectors",
]
