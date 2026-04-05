"""
Sync Engine — scheduled data synchronization from connectors to ontology.

Runs all configured connectors on a schedule (same pattern as standing
swarms in the bootstrap). Each connector pulls data from its source and
ingests it into the ontology as typed ObjectInstance records.

Usage:
    engine = SyncEngine(ontology, connectors)
    results = await engine.sync_all()
    # or
    result = await engine.sync_one("csv")
    # or background loop
    await engine.run_scheduled(interval_minutes=60)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.intelligence.connectors.base import SyncResult

logger = logging.getLogger(__name__)


class SyncEngine:
    """Scheduled data synchronization from connectors to ontology."""

    def __init__(
        self,
        ontology: Any,
        connectors: list[Any],
        default_type_mapping: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        ontology : InMemoryOntology
            The ontology to sync data into.
        connectors : list
            List of connector instances (each has .name, .connect(), .sync()).
        default_type_mapping : dict, optional
            Default column-to-property mappings keyed by connector name.
            Example: {"csv": {"Company": "name", "Revenue": "revenue"}}
            Individual sync calls can override this.
        """
        self._ontology = ontology
        self._connectors: dict[str, Any] = {c.name: c for c in connectors}
        self._default_mappings = default_type_mapping or {}
        self._last_sync: dict[str, SyncResult] = {}
        self._running = False

    @property
    def connector_names(self) -> list[str]:
        """Return names of all registered connectors."""
        return list(self._connectors.keys())

    @property
    def last_sync_results(self) -> dict[str, SyncResult]:
        """Return results from the most recent sync_all()."""
        return dict(self._last_sync)

    async def sync_all(
        self,
        type_mappings: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, SyncResult]:
        """Run all connectors and return results keyed by connector name.

        Parameters
        ----------
        type_mappings : dict, optional
            Connector-specific column-to-property mappings, keyed by
            connector name. Falls back to default_type_mapping, then
            to an empty dict (which means columns map 1:1).
        """
        mappings = type_mappings or self._default_mappings
        results: dict[str, SyncResult] = {}

        for name, connector in self._connectors.items():
            mapping = mappings.get(name, {})
            try:
                result = await self._run_connector(connector, mapping)
                results[name] = result
            except Exception as e:
                logger.error("SyncEngine: connector %s failed: %s", name, e)
                results[name] = SyncResult(errors=[str(e)])

        self._last_sync = results

        total_created = sum(r.objects_created for r in results.values())
        total_errors = sum(len(r.errors) for r in results.values())
        logger.info(
            "SyncEngine sync_all complete: %d connectors, %d objects created, %d errors",
            len(results),
            total_created,
            total_errors,
        )
        return results

    async def sync_one(
        self,
        connector_name: str,
        type_mapping: dict[str, str] | None = None,
    ) -> SyncResult:
        """Run a specific connector by name.

        Parameters
        ----------
        connector_name : str
            The name of the connector to run.
        type_mapping : dict, optional
            Column-to-property mapping for this connector.
        """
        connector = self._connectors.get(connector_name)
        if connector is None:
            return SyncResult(errors=[f"Unknown connector: {connector_name}"])

        mapping = type_mapping or self._default_mappings.get(connector_name, {})
        result = await self._run_connector(connector, mapping)
        self._last_sync[connector_name] = result
        return result

    async def run_scheduled(self, interval_minutes: int = 60) -> None:
        """Background loop — same pattern as standing swarms.

        Runs sync_all() repeatedly at the given interval. Call this as
        an asyncio task:

            task = asyncio.create_task(engine.run_scheduled(60))

        Cancel the task to stop the loop.
        """
        self._running = True
        logger.info(
            "SyncEngine scheduled loop started (interval=%d min, connectors=%s)",
            interval_minutes,
            list(self._connectors.keys()),
        )

        while self._running:
            try:
                results = await self.sync_all()
                summary = {
                    k: {"created": v.objects_created, "errors": len(v.errors)}
                    for k, v in results.items()
                }
                logger.info("SyncEngine scheduled sync: %s", summary)
            except Exception as e:
                logger.error("SyncEngine scheduled sync error: %s", e)

            await asyncio.sleep(interval_minutes * 60)

    def stop(self) -> None:
        """Signal the scheduled loop to stop after the current cycle."""
        self._running = False

    async def _run_connector(
        self,
        connector: Any,
        type_mapping: dict[str, str],
    ) -> SyncResult:
        """Connect, sync, and disconnect a single connector."""
        start = time.monotonic()

        # Connect
        connected = await connector.connect()
        if not connected:
            return SyncResult(
                errors=[f"Connector {connector.name} failed to connect"],
                duration_seconds=time.monotonic() - start,
            )

        # Sync
        try:
            result = await connector.sync(
                ontology=self._ontology,
                type_mapping=type_mapping,
            )
        except Exception as e:
            result = SyncResult(errors=[f"Sync error: {e}"])
        finally:
            await connector.disconnect()

        result.duration_seconds = time.monotonic() - start
        return result
