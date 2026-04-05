"""
Ontology — the core knowledge graph for the ForgeOS Intelligence Platform.

Represents a business as typed objects, properties, and relationships.
Agents operate on business entities (Customer, Deal, Invoice) instead of raw JSON.

Two backends:
- InMemoryOntology: dict-based for development / testing
- PostgresOntology: production backend using ontology_* tables (future)
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PropertyDef:
    """Definition of a single property on an object type."""
    name: str
    type: str = "string"        # "string" | "number" | "boolean" | "date" | "enum"
    required: bool = False
    enum_values: list[str] | None = None


@dataclass
class ObjectType:
    """A type of business entity (Customer, Lead, Invoice, Product)."""
    name: str
    properties: dict[str, PropertyDef] = field(default_factory=dict)
    description: str = ""
    icon: str = ""


@dataclass
class LinkType:
    """A relationship between two object types."""
    name: str
    from_type: str
    to_type: str
    cardinality: str = "one_to_many"  # "one_to_many" | "many_to_many" | "one_to_one"


# ---------------------------------------------------------------------------
# Instance dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ObjectInstance:
    """A concrete business object."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type_name: str = ""
    tenant_id: str = "default"
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = "manual"


@dataclass
class LinkInstance:
    """A concrete relationship between two objects."""
    from_id: str = ""
    to_id: str = ""
    link_type: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = "default"


# ---------------------------------------------------------------------------
# InMemoryOntology
# ---------------------------------------------------------------------------

class InMemoryOntology:
    """Dict-based ontology for development and testing."""

    MAX_TRAVERSAL_DEPTH = 10
    MAX_TRAVERSAL_RESULTS = 1000

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Schema
        self._types: dict[str, ObjectType] = {}
        self._link_types: dict[str, LinkType] = {}

        # Instances
        self._objects: dict[str, ObjectInstance] = {}
        self._links: list[LinkInstance] = []

        # Indexes
        self._objects_by_type: dict[str, list[str]] = defaultdict(list)
        self._links_from: dict[str, list[int]] = defaultdict(list)   # obj_id -> link indexes
        self._links_to: dict[str, list[int]] = defaultdict(list)     # obj_id -> link indexes

    # -- Schema registration -------------------------------------------------

    def register_type(self, obj_type: ObjectType) -> None:
        """Register an object type in the ontology schema."""
        with self._lock:
            self._types[obj_type.name] = obj_type
        logger.debug("Registered type: %s", obj_type.name)

    def register_link_type(self, link: LinkType) -> None:
        """Register a link (relationship) type in the ontology schema."""
        with self._lock:
            self._link_types[link.name] = link
        logger.debug("Registered link type: %s (%s -> %s)", link.name, link.from_type, link.to_type)

    # -- Object CRUD ----------------------------------------------------------

    def upsert_object(self, obj: ObjectInstance) -> str:
        """Insert or update an object. Returns the object ID."""
        with self._lock:
            return self._upsert_object_unlocked(obj)

    def _upsert_object_unlocked(self, obj: ObjectInstance) -> str:
        now = datetime.now(timezone.utc).isoformat()
        if obj.id in self._objects:
            existing = self._objects[obj.id]
            existing.properties.update(obj.properties)
            existing.updated_at = now
            existing.source = obj.source or existing.source
        else:
            obj.updated_at = now
            self._objects[obj.id] = obj
            self._objects_by_type[obj.type_name].append(obj.id)
        return obj.id

    def get_object(self, obj_id: str) -> ObjectInstance | None:
        """Get an object by ID."""
        return self._objects.get(obj_id)

    def query_objects(
        self,
        type_name: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[ObjectInstance]:
        """Query objects by type with optional property filters."""
        obj_ids = self._objects_by_type.get(type_name, [])
        results: list[ObjectInstance] = []
        for oid in obj_ids:
            obj = self._objects[oid]
            if filters:
                match = True
                for key, value in filters.items():
                    if obj.properties.get(key) != value:
                        match = False
                        break
                if not match:
                    continue
            results.append(obj)
            if len(results) >= limit:
                break
        return results

    # -- Link CRUD ------------------------------------------------------------

    def upsert_link(self, link: LinkInstance) -> None:
        """Create a link between two objects."""
        with self._lock:
            self._upsert_link_unlocked(link)

    def _upsert_link_unlocked(self, link: LinkInstance) -> None:
        # Avoid duplicate links
        for existing in self._links:
            if (
                existing.from_id == link.from_id
                and existing.to_id == link.to_id
                and existing.link_type == link.link_type
            ):
                existing.properties.update(link.properties)
                return

        idx = len(self._links)
        self._links.append(link)
        self._links_from[link.from_id].append(idx)
        self._links_to[link.to_id].append(idx)

    def get_neighbors(
        self,
        obj_id: str,
        link_type: str | None = None,
        depth: int = 1,
    ) -> list[ObjectInstance]:
        """Get neighboring objects via links, optionally filtered by link type.

        Depth is capped at MAX_TRAVERSAL_DEPTH (10). Results capped at MAX_TRAVERSAL_RESULTS (1000).

        Supports multi-hop traversal via the depth parameter.
        """
        depth = min(depth, self.MAX_TRAVERSAL_DEPTH)
        visited: set[str] = {obj_id}
        current_frontier: set[str] = {obj_id}
        results: list[ObjectInstance] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for cid in current_frontier:
                # Outgoing links
                for link_idx in self._links_from.get(cid, []):
                    link = self._links[link_idx]
                    if link_type and link.link_type != link_type:
                        continue
                    if link.to_id not in visited:
                        visited.add(link.to_id)
                        next_frontier.add(link.to_id)
                        obj = self._objects.get(link.to_id)
                        if obj:
                            results.append(obj)

                # Incoming links
                for link_idx in self._links_to.get(cid, []):
                    link = self._links[link_idx]
                    if link_type and link.link_type != link_type:
                        continue
                    if link.from_id not in visited:
                        visited.add(link.from_id)
                        next_frontier.add(link.from_id)
                        obj = self._objects.get(link.from_id)
                        if obj:
                            results.append(obj)

            current_frontier = next_frontier
            if not current_frontier or len(results) >= self.MAX_TRAVERSAL_RESULTS:
                break

        return results[:self.MAX_TRAVERSAL_RESULTS]

    # -- Search ---------------------------------------------------------------

    def search(
        self,
        query: str,
        type_name: str | None = None,
    ) -> list[ObjectInstance]:
        """Text search across object properties. Case-insensitive substring match."""
        query_lower = query.lower()
        results: list[ObjectInstance] = []

        candidates = self._objects.values()
        if type_name:
            candidate_ids = self._objects_by_type.get(type_name, [])
            candidates = [self._objects[oid] for oid in candidate_ids]

        for obj in candidates:
            for value in obj.properties.values():
                if isinstance(value, str) and query_lower in value.lower():
                    results.append(obj)
                    break

        return results

    # -- Aggregation ----------------------------------------------------------

    def aggregate(
        self,
        type_name: str,
        metric: str = "count",
        group_by: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate objects by type.

        Metrics: count, sum, avg (sum/avg require group_by to be a numeric property).
        Returns: {"total": N, "groups": {group_value: metric_value}} if group_by,
                 or {"total": N} if no group_by.
        """
        objects = self.query_objects(type_name, limit=100_000)

        if not group_by:
            if metric == "count":
                return {"total": len(objects)}
            # sum/avg on all objects doesn't make sense without a numeric field
            return {"total": len(objects)}

        groups: dict[str, list[Any]] = defaultdict(list)
        for obj in objects:
            key = str(obj.properties.get(group_by, "unknown"))
            groups[key].append(obj)

        if metric == "count":
            group_results = {k: len(v) for k, v in groups.items()}
        elif metric == "sum":
            group_results = {}
            for k, objs in groups.items():
                total = sum(
                    obj.properties.get(group_by, 0)
                    for obj in objs
                    if isinstance(obj.properties.get(group_by), (int, float))
                )
                group_results[k] = total
        elif metric == "avg":
            group_results = {}
            for k, objs in groups.items():
                values = [
                    obj.properties.get(group_by, 0)
                    for obj in objs
                    if isinstance(obj.properties.get(group_by), (int, float))
                ]
                group_results[k] = sum(values) / len(values) if values else 0
        else:
            group_results = {k: len(v) for k, v in groups.items()}

        return {"total": len(objects), "groups": group_results}

    # -- Schema introspection -------------------------------------------------

    def get_schema(self) -> dict[str, Any]:
        """Return the full ontology schema (types + link types)."""
        return {
            "types": [
                {
                    "name": t.name,
                    "description": t.description,
                    "icon": t.icon,
                    "properties": {
                        name: {
                            "type": p.type,
                            "required": p.required,
                            **({"enum_values": p.enum_values} if p.enum_values else {}),
                        }
                        for name, p in t.properties.items()
                    },
                }
                for t in self._types.values()
            ],
            "link_types": [
                {
                    "name": lt.name,
                    "from_type": lt.from_type,
                    "to_type": lt.to_type,
                    "cardinality": lt.cardinality,
                }
                for lt in self._link_types.values()
            ],
        }

    def get_types(self) -> list[ObjectType]:
        """Return all registered object types."""
        return list(self._types.values())

    def get_link_types(self) -> list[LinkType]:
        """Return all registered link types."""
        return list(self._link_types.values())

    # -- YAML schema loader ---------------------------------------------------

    def load_schema(self, path: str | Path) -> None:
        """Load object types and link types from a YAML schema file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Ontology schema not found: {path}")

        with open(path) as f:
            schema = yaml.safe_load(f)

        # Validate schema structure
        if not isinstance(schema, dict):
            raise ValueError(f"Schema must be a dict, got {type(schema)}")
        types_list = schema.get("types")
        if not isinstance(types_list, list):
            raise ValueError("Schema must have a 'types' list")
        for t in types_list:
            if "name" not in t:
                raise ValueError(f"Type definition missing 'name': {t}")
        type_names = {t["name"] for t in types_list}
        for link in schema.get("links", []):
            for req_key in ("name", "from", "to"):
                if req_key not in link:
                    raise ValueError(f"Link missing '{req_key}': {link}")
            if link["from"] not in type_names:
                raise ValueError(f"Link '{link['name']}' references unknown type '{link['from']}'")
            if link["to"] not in type_names:
                raise ValueError(f"Link '{link['name']}' references unknown type '{link['to']}'")

        # Register object types
        for type_def in schema.get("types", []):
            props: dict[str, PropertyDef] = {}
            for pname, pdef in type_def.get("properties", {}).items():
                if isinstance(pdef, dict):
                    props[pname] = PropertyDef(
                        name=pname,
                        type=pdef.get("type", "string"),
                        required=pdef.get("required", False),
                        enum_values=pdef.get("values"),
                    )
                else:
                    props[pname] = PropertyDef(name=pname, type=str(pdef))

            self.register_type(ObjectType(
                name=type_def["name"],
                properties=props,
                description=type_def.get("description", ""),
                icon=type_def.get("icon", ""),
            ))

        # Register link types
        for link_def in schema.get("links", []):
            self.register_link_type(LinkType(
                name=link_def["name"],
                from_type=link_def["from"],
                to_type=link_def["to"],
                cardinality=link_def.get("cardinality", "one_to_many"),
            ))

        logger.info(
            "Loaded schema from %s: %d types, %d link types",
            path.name, len(self._types), len(self._link_types),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_ontology(db_client: Any | None = None) -> InMemoryOntology:
    """Create an ontology instance.

    Returns InMemoryOntology for now. When a PostgreSQL db_client is provided,
    a future PostgresOntology backend can be returned instead.
    """
    # Future: if db_client: return PostgresOntology(db_client)
    return InMemoryOntology()
