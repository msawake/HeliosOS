"""
Ontology tools for intelligence agents.

Provides 5 tools that agents use to query the business knowledge graph:
- ontology_query_objects: search/filter objects by type and properties
- ontology_get_neighbors: traverse relationships from an object
- ontology_aggregate: count, sum, avg by type and optional group_by
- ontology_search: text search across all object properties
- ontology_get_schema: understand available types and relationships
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from src.intelligence.ontology import InMemoryOntology, ObjectInstance

logger = logging.getLogger(__name__)


class OntologyTools:
    """Agent-facing tools for querying the ontology knowledge graph."""

    def __init__(self, ontology: InMemoryOntology) -> None:
        self._ontology = ontology

    # -- Tool implementations -------------------------------------------------

    def query_objects(
        self,
        type_name: str,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query objects by type with optional property filters."""
        objects = self._ontology.query_objects(type_name, filters=filters, limit=limit)
        return [self._serialize(obj) for obj in objects]

    def get_neighbors(
        self,
        object_id: str,
        link_type: str | None = None,
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Traverse relationships from an object."""
        neighbors = self._ontology.get_neighbors(object_id, link_type=link_type, depth=depth)
        return [self._serialize(obj) for obj in neighbors]

    def aggregate(
        self,
        type_name: str,
        group_by: str | None = None,
        metric: str = "count",
    ) -> dict[str, Any]:
        """Aggregate objects: count, sum, or avg by type with optional group_by."""
        return self._ontology.aggregate(type_name, metric=metric, group_by=group_by)

    def search(
        self,
        query: str,
        type_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Text search across object properties."""
        results = self._ontology.search(query, type_name=type_name)
        return [self._serialize(obj) for obj in results]

    def get_schema(self) -> dict[str, Any]:
        """Return the full ontology schema (types + link types)."""
        return self._ontology.get_schema()

    # -- Tool definitions (Anthropic format) ----------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return Anthropic-format tool schemas for all ontology tools."""
        return [
            {
                "name": "ontology_query_objects",
                "description": (
                    "Query business objects by type with optional property filters. "
                    "Use this to find customers, leads, deals, invoices, etc."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "type_name": {
                            "type": "string",
                            "description": "The object type to query (e.g. 'Customer', 'Lead')",
                        },
                        "filters": {
                            "type": "object",
                            "description": "Optional property filters as key-value pairs",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default 50)",
                            "default": 50,
                        },
                    },
                    "required": ["type_name"],
                },
            },
            {
                "name": "ontology_get_neighbors",
                "description": (
                    "Traverse relationships from a specific object. "
                    "Returns connected objects (e.g. a customer's leads, a deal's invoices)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "object_id": {
                            "type": "string",
                            "description": "The ID of the object to traverse from",
                        },
                        "link_type": {
                            "type": "string",
                            "description": "Optional link type filter (e.g. 'has_lead')",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Traversal depth (default 1, max 3)",
                            "default": 1,
                        },
                    },
                    "required": ["object_id"],
                },
            },
            {
                "name": "ontology_aggregate",
                "description": (
                    "Aggregate business objects — count, sum, or average. "
                    "Useful for metrics like 'how many active customers' or "
                    "'total revenue by industry'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "type_name": {
                            "type": "string",
                            "description": "The object type to aggregate",
                        },
                        "group_by": {
                            "type": "string",
                            "description": "Optional property to group by",
                        },
                        "metric": {
                            "type": "string",
                            "description": "Aggregation metric: 'count', 'sum', or 'avg'",
                            "enum": ["count", "sum", "avg"],
                            "default": "count",
                        },
                    },
                    "required": ["type_name"],
                },
            },
            {
                "name": "ontology_search",
                "description": (
                    "Full-text search across all object properties. "
                    "Finds objects matching a query string (case-insensitive)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query string",
                        },
                        "type_name": {
                            "type": "string",
                            "description": "Optional type filter to narrow search",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "ontology_get_schema",
                "description": (
                    "Get the ontology schema — all object types, their properties, "
                    "and relationship definitions. Call this first to understand "
                    "what data is available."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    # -- Tool router ----------------------------------------------------------

    def execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Route a tool call to the appropriate method."""
        router = {
            "ontology_query_objects": lambda inp: {
                "objects": self.query_objects(
                    type_name=inp["type_name"],
                    filters=inp.get("filters"),
                    limit=inp.get("limit", 50),
                ),
            },
            "ontology_get_neighbors": lambda inp: {
                "neighbors": self.get_neighbors(
                    object_id=inp["object_id"],
                    link_type=inp.get("link_type"),
                    depth=inp.get("depth", 1),
                ),
            },
            "ontology_aggregate": lambda inp: self.aggregate(
                type_name=inp["type_name"],
                group_by=inp.get("group_by"),
                metric=inp.get("metric", "count"),
            ),
            "ontology_search": lambda inp: {
                "results": self.search(
                    query=inp["query"],
                    type_name=inp.get("type_name"),
                ),
            },
            "ontology_get_schema": lambda inp: self.get_schema(),
        }

        handler = router.get(tool_name)
        if not handler:
            return {"error": f"Unknown ontology tool: {tool_name}"}

        try:
            return handler(tool_input)
        except Exception as e:
            logger.error("Ontology tool %s failed: %s", tool_name, e)
            return {"error": str(e)}

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _serialize(obj: ObjectInstance) -> dict[str, Any]:
        """Convert an ObjectInstance to a dict for agent consumption."""
        return {
            "id": obj.id,
            "type": obj.type_name,
            "properties": obj.properties,
            "source": obj.source,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
        }
