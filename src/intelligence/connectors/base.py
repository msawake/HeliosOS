"""
Base definitions for data connectors.

DataConnector is the protocol all connectors implement.
SyncResult captures the outcome of a sync operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Outcome of a connector sync operation."""

    objects_created: int = 0
    objects_updated: int = 0
    links_created: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_objects(self) -> int:
        return self.objects_created + self.objects_updated

    def __repr__(self) -> str:
        return (
            f"SyncResult(created={self.objects_created}, updated={self.objects_updated}, "
            f"links={self.links_created}, errors={len(self.errors)}, "
            f"duration={self.duration_seconds:.2f}s)"
        )


@runtime_checkable
class DataConnector(Protocol):
    """Interface all data connectors implement.

    Connectors pull data from external sources and ingest it into
    the ontology as typed ObjectInstance records.
    """

    name: str
    supported_types: list[str]

    async def connect(self, credentials: dict[str, Any] | None = None) -> bool:
        """Establish connection to the data source.

        Returns True if connected successfully.
        """
        ...

    async def discover_schema(self) -> dict[str, Any]:
        """Discover what data is available in the source.

        Returns a dict describing available tables/sheets/endpoints
        and their columns/fields.
        """
        ...

    async def sync(
        self,
        ontology: Any,
        type_mapping: dict[str, str],
        since: str | None = None,
    ) -> SyncResult:
        """Sync data from the source into the ontology.

        Parameters
        ----------
        ontology : InMemoryOntology
            The ontology to ingest objects into.
        type_mapping : dict[str, str]
            Maps source field names to ontology property names.
            Example: {"Company Name": "name", "ARR": "revenue"}
        since : str, optional
            ISO timestamp for incremental sync. If provided, only sync
            records modified after this time.

        Returns
        -------
        SyncResult
            Counts of objects created/updated, links created, errors.
        """
        ...

    async def disconnect(self) -> None:
        """Close connection to the data source."""
        ...
