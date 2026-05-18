"""Tests for the ForgeOS Intelligence Platform — Ontology layer."""

import os
from pathlib import Path

import pytest

from src.intelligence.ontology import (
    InMemoryOntology,
    LinkInstance,
    LinkType,
    ObjectInstance,
    ObjectType,
    PropertyDef,
    create_ontology,
)
from src.intelligence.tools import OntologyTools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "src" / "intelligence" / "schemas"


def _make_ontology_with_data() -> InMemoryOntology:
    """Create an ontology with sample types, objects, and links for testing."""
    onto = InMemoryOntology()

    # Register types
    onto.register_type(ObjectType(
        name="Customer",
        properties={
            "name": PropertyDef(name="name", type="string", required=True),
            "industry": PropertyDef(name="industry", type="string"),
            "revenue": PropertyDef(name="revenue", type="number"),
            "stage": PropertyDef(
                name="stage", type="enum", enum_values=["prospect", "active", "churned"],
            ),
        },
        description="A B2B customer",
    ))
    onto.register_type(ObjectType(
        name="Lead",
        properties={
            "email": PropertyDef(name="email", type="string", required=True),
            "score": PropertyDef(name="score", type="number"),
            "status": PropertyDef(
                name="status", type="enum", enum_values=["new", "mql", "sql", "won", "lost"],
            ),
        },
        description="A sales lead",
    ))
    onto.register_type(ObjectType(
        name="Deal",
        properties={
            "name": PropertyDef(name="name", type="string", required=True),
            "value": PropertyDef(name="value", type="number"),
        },
    ))

    # Register link types
    onto.register_link_type(LinkType(
        name="has_lead", from_type="Customer", to_type="Lead", cardinality="one_to_many",
    ))
    onto.register_link_type(LinkType(
        name="has_deal", from_type="Customer", to_type="Deal", cardinality="one_to_many",
    ))
    onto.register_link_type(LinkType(
        name="owns_deal", from_type="Lead", to_type="Deal", cardinality="one_to_many",
    ))

    # Create objects
    onto.upsert_object(ObjectInstance(
        id="cust-1", type_name="Customer",
        properties={"name": "Acme Corp", "industry": "Technology", "revenue": 5_000_000, "stage": "active"},
    ))
    onto.upsert_object(ObjectInstance(
        id="cust-2", type_name="Customer",
        properties={"name": "Globex Inc", "industry": "Manufacturing", "revenue": 2_000_000, "stage": "active"},
    ))
    onto.upsert_object(ObjectInstance(
        id="cust-3", type_name="Customer",
        properties={"name": "Initech", "industry": "Technology", "revenue": 500_000, "stage": "churned"},
    ))
    onto.upsert_object(ObjectInstance(
        id="lead-1", type_name="Lead",
        properties={"email": "alice@acme.com", "score": 85, "status": "sql"},
    ))
    onto.upsert_object(ObjectInstance(
        id="lead-2", type_name="Lead",
        properties={"email": "bob@globex.com", "score": 40, "status": "mql"},
    ))
    onto.upsert_object(ObjectInstance(
        id="deal-1", type_name="Deal",
        properties={"name": "Acme Enterprise License", "value": 120_000},
    ))
    onto.upsert_object(ObjectInstance(
        id="deal-2", type_name="Deal",
        properties={"name": "Globex Starter Plan", "value": 25_000},
    ))

    # Create links
    onto.upsert_link(LinkInstance(from_id="cust-1", to_id="lead-1", link_type="has_lead"))
    onto.upsert_link(LinkInstance(from_id="cust-2", to_id="lead-2", link_type="has_lead"))
    onto.upsert_link(LinkInstance(from_id="cust-1", to_id="deal-1", link_type="has_deal"))
    onto.upsert_link(LinkInstance(from_id="cust-2", to_id="deal-2", link_type="has_deal"))
    onto.upsert_link(LinkInstance(from_id="lead-1", to_id="deal-1", link_type="owns_deal"))

    return onto


# ---------------------------------------------------------------------------
# Tests: Schema Registration
# ---------------------------------------------------------------------------

class TestRegisterType:
    def test_register_type(self):
        onto = InMemoryOntology()
        obj_type = ObjectType(
            name="Customer",
            properties={"name": PropertyDef(name="name", type="string", required=True)},
            description="A customer",
            icon="building",
        )
        onto.register_type(obj_type)

        types = onto.get_types()
        assert len(types) == 1
        assert types[0].name == "Customer"
        assert "name" in types[0].properties
        assert types[0].properties["name"].required is True

    def test_register_multiple_types(self):
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Customer"))
        onto.register_type(ObjectType(name="Lead"))
        onto.register_type(ObjectType(name="Deal"))
        assert len(onto.get_types()) == 3

    def test_register_type_overwrites(self):
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Customer", description="v1"))
        onto.register_type(ObjectType(name="Customer", description="v2"))
        types = onto.get_types()
        assert len(types) == 1
        assert types[0].description == "v2"


class TestRegisterLinkType:
    def test_register_link_type(self):
        onto = InMemoryOntology()
        link = LinkType(
            name="has_lead", from_type="Customer", to_type="Lead", cardinality="one_to_many",
        )
        onto.register_link_type(link)

        link_types = onto.get_link_types()
        assert len(link_types) == 1
        assert link_types[0].name == "has_lead"
        assert link_types[0].from_type == "Customer"
        assert link_types[0].to_type == "Lead"

    def test_register_multiple_link_types(self):
        onto = InMemoryOntology()
        onto.register_link_type(LinkType(name="has_lead", from_type="Customer", to_type="Lead"))
        onto.register_link_type(LinkType(name="has_deal", from_type="Customer", to_type="Deal"))
        assert len(onto.get_link_types()) == 2


# ---------------------------------------------------------------------------
# Tests: Object CRUD
# ---------------------------------------------------------------------------

class TestUpsertAndGetObject:
    def test_upsert_and_get_object(self):
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Customer"))

        obj_id = onto.upsert_object(ObjectInstance(
            id="cust-1", type_name="Customer",
            properties={"name": "Acme Corp", "revenue": 5_000_000},
        ))

        assert obj_id == "cust-1"
        result = onto.get_object("cust-1")
        assert result is not None
        assert result.properties["name"] == "Acme Corp"
        assert result.properties["revenue"] == 5_000_000

    def test_upsert_updates_existing(self):
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Customer"))

        onto.upsert_object(ObjectInstance(
            id="cust-1", type_name="Customer",
            properties={"name": "Acme Corp", "revenue": 5_000_000},
        ))
        onto.upsert_object(ObjectInstance(
            id="cust-1", type_name="Customer",
            properties={"revenue": 6_000_000},
        ))

        result = onto.get_object("cust-1")
        assert result is not None
        assert result.properties["name"] == "Acme Corp"  # preserved
        assert result.properties["revenue"] == 6_000_000  # updated

    def test_get_nonexistent_returns_none(self):
        onto = InMemoryOntology()
        assert onto.get_object("does-not-exist") is None

    def test_auto_generated_id(self):
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Lead"))

        obj = ObjectInstance(type_name="Lead", properties={"email": "test@example.com"})
        obj_id = onto.upsert_object(obj)
        assert obj_id is not None
        assert len(obj_id) > 0
        assert onto.get_object(obj_id) is not None


# ---------------------------------------------------------------------------
# Tests: Query Objects
# ---------------------------------------------------------------------------

class TestQueryObjectsWithFilters:
    def test_query_objects_with_filters(self):
        onto = _make_ontology_with_data()

        # All customers
        customers = onto.query_objects("Customer")
        assert len(customers) == 3

        # Filter by industry
        tech = onto.query_objects("Customer", filters={"industry": "Technology"})
        assert len(tech) == 2
        assert all(c.properties["industry"] == "Technology" for c in tech)

        # Filter by stage
        active = onto.query_objects("Customer", filters={"stage": "active"})
        assert len(active) == 2

        churned = onto.query_objects("Customer", filters={"stage": "churned"})
        assert len(churned) == 1
        assert churned[0].properties["name"] == "Initech"

    def test_query_with_limit(self):
        onto = _make_ontology_with_data()
        results = onto.query_objects("Customer", limit=2)
        assert len(results) == 2

    def test_query_empty_type(self):
        onto = _make_ontology_with_data()
        results = onto.query_objects("NonExistentType")
        assert len(results) == 0

    def test_query_multiple_filters(self):
        onto = _make_ontology_with_data()
        results = onto.query_objects("Customer", filters={"industry": "Technology", "stage": "active"})
        assert len(results) == 1
        assert results[0].properties["name"] == "Acme Corp"


# ---------------------------------------------------------------------------
# Tests: Links and Neighbors
# ---------------------------------------------------------------------------

class TestUpsertLinkAndGetNeighbors:
    def test_upsert_link_and_get_neighbors(self):
        onto = _make_ontology_with_data()

        neighbors = onto.get_neighbors("cust-1")
        assert len(neighbors) == 2  # lead-1, deal-1

        neighbor_ids = {n.id for n in neighbors}
        assert "lead-1" in neighbor_ids
        assert "deal-1" in neighbor_ids

    def test_get_neighbors_with_link_type_filter(self):
        onto = _make_ontology_with_data()

        leads = onto.get_neighbors("cust-1", link_type="has_lead")
        assert len(leads) == 1
        assert leads[0].id == "lead-1"

        deals = onto.get_neighbors("cust-1", link_type="has_deal")
        assert len(deals) == 1
        assert deals[0].id == "deal-1"

    def test_get_neighbors_reverse_direction(self):
        onto = _make_ontology_with_data()

        # lead-1 should find cust-1 (incoming link) and deal-1 (outgoing link)
        neighbors = onto.get_neighbors("lead-1")
        neighbor_ids = {n.id for n in neighbors}
        assert "cust-1" in neighbor_ids
        assert "deal-1" in neighbor_ids

    def test_no_neighbors(self):
        onto = _make_ontology_with_data()
        neighbors = onto.get_neighbors("cust-3")  # churned, no links
        assert len(neighbors) == 0

    def test_duplicate_link_updates_properties(self):
        onto = _make_ontology_with_data()

        # Upsert duplicate link with new properties
        onto.upsert_link(LinkInstance(
            from_id="cust-1", to_id="lead-1", link_type="has_lead",
            properties={"relationship_strength": "strong"},
        ))

        # Should still be same number of neighbors (no duplicate link)
        neighbors = onto.get_neighbors("cust-1", link_type="has_lead")
        assert len(neighbors) == 1


class TestDepthTraversal:
    def test_depth_traversal(self):
        onto = _make_ontology_with_data()

        # Depth 1 from cust-1: lead-1, deal-1
        depth1 = onto.get_neighbors("cust-1", depth=1)
        assert len(depth1) == 2

        # Depth 2 from cust-1: should also pick up deal-1 via lead-1->deal-1
        # But deal-1 is already found at depth 1, so the unique set stays the same
        # unless there are objects only reachable at depth 2
        depth2 = onto.get_neighbors("cust-1", depth=2)
        # lead-1 connects to cust-1 (visited) and deal-1 (already visited)
        # deal-1 connects to lead-1 (via owns_deal reverse) and cust-1 (visited)
        # So depth 2 shouldn't add more here
        assert len(depth2) >= 2  # at least same as depth 1

    def test_depth_traversal_chain(self):
        """Test a linear chain: A -> B -> C -> D at increasing depths."""
        onto = InMemoryOntology()
        onto.register_type(ObjectType(name="Node"))
        onto.register_link_type(LinkType(name="next", from_type="Node", to_type="Node"))

        for i in range(5):
            onto.upsert_object(ObjectInstance(
                id=f"node-{i}", type_name="Node", properties={"label": f"Node {i}"},
            ))

        for i in range(4):
            onto.upsert_link(LinkInstance(
                from_id=f"node-{i}", to_id=f"node-{i+1}", link_type="next",
            ))

        # From node-0, depth 1 => node-1
        d1 = onto.get_neighbors("node-0", depth=1)
        assert len(d1) == 1
        assert d1[0].id == "node-1"

        # Depth 2 => node-1, node-2
        d2 = onto.get_neighbors("node-0", depth=2)
        assert len(d2) == 2

        # Depth 4 => node-1, node-2, node-3, node-4
        d4 = onto.get_neighbors("node-0", depth=4)
        assert len(d4) == 4


# ---------------------------------------------------------------------------
# Tests: Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search(self):
        onto = _make_ontology_with_data()

        results = onto.search("Acme")
        assert len(results) >= 1
        assert any(r.properties.get("name") == "Acme Corp" for r in results)

    def test_search_case_insensitive(self):
        onto = _make_ontology_with_data()

        results = onto.search("acme")
        assert len(results) >= 1
        assert any(r.properties.get("name") == "Acme Corp" for r in results)

    def test_search_with_type_filter(self):
        onto = _make_ontology_with_data()

        # "Acme" appears in Customer and Deal
        all_results = onto.search("Acme")
        customer_results = onto.search("Acme", type_name="Customer")
        deal_results = onto.search("Acme", type_name="Deal")

        assert len(customer_results) >= 1
        assert all(r.type_name == "Customer" for r in customer_results)

        assert len(deal_results) >= 1
        assert all(r.type_name == "Deal" for r in deal_results)

    def test_search_no_results(self):
        onto = _make_ontology_with_data()
        results = onto.search("NonExistentThing12345")
        assert len(results) == 0

    def test_search_email(self):
        onto = _make_ontology_with_data()
        results = onto.search("alice@acme.com")
        assert len(results) == 1
        assert results[0].id == "lead-1"


# ---------------------------------------------------------------------------
# Tests: Aggregation
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_aggregate(self):
        onto = _make_ontology_with_data()

        result = onto.aggregate("Customer")
        assert result["total"] == 3

    def test_aggregate_with_group_by(self):
        onto = _make_ontology_with_data()

        result = onto.aggregate("Customer", group_by="industry")
        assert result["total"] == 3
        assert "groups" in result
        assert result["groups"]["Technology"] == 2
        assert result["groups"]["Manufacturing"] == 1

    def test_aggregate_by_stage(self):
        onto = _make_ontology_with_data()

        result = onto.aggregate("Customer", group_by="stage")
        assert result["groups"]["active"] == 2
        assert result["groups"]["churned"] == 1

    def test_aggregate_empty_type(self):
        onto = _make_ontology_with_data()
        result = onto.aggregate("NonExistentType")
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Tests: YAML Schema Loading
# ---------------------------------------------------------------------------

class TestLoadSchemaFromYaml:
    def test_load_schema_from_yaml(self):
        onto = InMemoryOntology()
        schema_path = SCHEMA_DIR / "b2b_sales.yaml"
        onto.load_schema(schema_path)

        types = onto.get_types()
        type_names = {t.name for t in types}
        assert "Customer" in type_names
        assert "Lead" in type_names
        assert "Campaign" in type_names
        assert "Deal" in type_names
        assert "Invoice" in type_names
        assert "Meeting" in type_names
        assert len(types) >= 6

        link_types = onto.get_link_types()
        link_names = {lt.name for lt in link_types}
        assert "has_lead" in link_names
        assert "has_deal" in link_names
        assert "targeted_by" in link_names
        assert len(link_types) >= 5

    def test_load_schema_properties_parsed(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "b2b_sales.yaml")

        types = {t.name: t for t in onto.get_types()}
        customer = types["Customer"]

        assert "name" in customer.properties
        assert customer.properties["name"].required is True
        assert customer.properties["name"].type == "string"

        assert "stage" in customer.properties
        assert customer.properties["stage"].type == "enum"
        assert "active" in customer.properties["stage"].enum_values

    def test_load_deals_schema(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "deals.yaml")
        type_names = {t.name for t in onto.get_types()}
        assert "Listing" in type_names
        assert "Buyer" in type_names
        assert "Seller" in type_names
        assert "Transaction" in type_names
        assert len(onto.get_link_types()) >= 5

    def test_load_travel_schema(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "travel.yaml")
        type_names = {t.name for t in onto.get_types()}
        assert "Traveler" in type_names
        assert "Booking" in type_names
        assert "Flight" in type_names
        assert "Hotel" in type_names
        assert len(onto.get_link_types()) >= 5

    def test_load_insurance_schema(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "insurance.yaml")
        type_names = {t.name for t in onto.get_types()}
        assert "Policyholder" in type_names
        assert "Policy" in type_names
        assert "Claim" in type_names
        assert len(onto.get_link_types()) >= 5

    def test_load_real_estate_schema(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "real_estate.yaml")
        type_names = {t.name for t in onto.get_types()}
        assert "Buyer" in type_names
        assert "Property" in type_names
        assert "Listing" in type_names
        assert "Mortgage" in type_names
        assert len(onto.get_link_types()) >= 5

    def test_load_nonexistent_schema_raises(self):
        onto = InMemoryOntology()
        with pytest.raises(FileNotFoundError):
            onto.load_schema("/nonexistent/path.yaml")

    def test_get_schema_after_load(self):
        onto = InMemoryOntology()
        onto.load_schema(SCHEMA_DIR / "b2b_sales.yaml")

        schema = onto.get_schema()
        assert "types" in schema
        assert "link_types" in schema
        assert len(schema["types"]) >= 6
        assert len(schema["link_types"]) >= 5


# ---------------------------------------------------------------------------
# Tests: OntologyTools routing
# ---------------------------------------------------------------------------

class TestOntologyToolsRouting:
    def test_ontology_tools_routing(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        # Test query_objects
        result = tools.execute_tool("ontology_query_objects", {"type_name": "Customer"})
        assert "objects" in result
        assert len(result["objects"]) == 3

        # Test with filters
        result = tools.execute_tool("ontology_query_objects", {
            "type_name": "Customer",
            "filters": {"stage": "active"},
        })
        assert len(result["objects"]) == 2

    def test_tools_get_neighbors(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        result = tools.execute_tool("ontology_get_neighbors", {"object_id": "cust-1"})
        assert "neighbors" in result
        assert len(result["neighbors"]) == 2

    def test_tools_aggregate(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        result = tools.execute_tool("ontology_aggregate", {"type_name": "Customer"})
        assert result["total"] == 3

        result = tools.execute_tool("ontology_aggregate", {
            "type_name": "Customer",
            "group_by": "industry",
        })
        assert result["groups"]["Technology"] == 2

    def test_tools_search(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        result = tools.execute_tool("ontology_search", {"query": "Acme"})
        assert "results" in result
        assert len(result["results"]) >= 1

    def test_tools_get_schema(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        result = tools.execute_tool("ontology_get_schema", {})
        assert "types" in result
        assert "link_types" in result

    def test_tools_unknown_tool(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        result = tools.execute_tool("unknown_tool", {})
        assert "error" in result

    def test_get_tool_definitions(self):
        onto = _make_ontology_with_data()
        tools = OntologyTools(onto)

        definitions = tools.get_tool_definitions()
        assert len(definitions) == 5

        names = {d["name"] for d in definitions}
        assert "ontology_query_objects" in names
        assert "ontology_get_neighbors" in names
        assert "ontology_aggregate" in names
        assert "ontology_search" in names
        assert "ontology_get_schema" in names

        # Each definition should have proper Anthropic format
        for defn in definitions:
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn
            assert defn["input_schema"]["type"] == "object"


# ---------------------------------------------------------------------------
# Tests: Factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_create_ontology_returns_inmemory(self):
        onto = create_ontology()
        assert isinstance(onto, InMemoryOntology)

    def test_create_ontology_with_none_db(self):
        onto = create_ontology(db_client=None)
        assert isinstance(onto, InMemoryOntology)
