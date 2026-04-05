"""Tests for intelligence data connectors and sync engine."""

import os
import tempfile
from pathlib import Path

import pytest

from src.intelligence.ontology import InMemoryOntology, ObjectType, PropertyDef
from src.intelligence.connectors.base import SyncResult
from src.intelligence.connectors.csv_connector import CSVConnector
from src.intelligence.connectors.api_connector import GenericAPIConnector
from src.intelligence.connectors.sheets_connector import GoogleSheetsConnector
from src.intelligence.connectors.db_connector import DatabaseConnector
from src.intelligence.connectors import create_connectors
from src.intelligence.sync_engine import SyncEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ontology() -> InMemoryOntology:
    """Create an ontology with a Customer type registered."""
    onto = InMemoryOntology()
    onto.register_type(ObjectType(
        name="Customer",
        properties={
            "name": PropertyDef(name="name", type="string", required=True),
            "industry": PropertyDef(name="industry", type="string"),
            "revenue": PropertyDef(name="revenue", type="number"),
            "employees": PropertyDef(name="employees", type="number"),
        },
        description="A business customer",
    ))
    return onto


def _write_csv(directory: Path, filename: str, content: str) -> Path:
    """Write a CSV file to the given directory."""
    filepath = directory / filename
    filepath.write_text(content, encoding="utf-8")
    return filepath


# ---------------------------------------------------------------------------
# CSV Connector
# ---------------------------------------------------------------------------

class TestCSVConnector:
    async def test_csv_connector_sync(self):
        """Create a temp CSV, sync to in-memory ontology, verify objects created."""
        onto = _make_ontology()

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(Path(tmpdir), "customers.csv", (
                "Company Name,Industry,Annual Revenue,Employees\n"
                "Acme Corp,Technology,5000000,250\n"
                "GlobalTech,Finance,3200000,180\n"
                "DataFlow Inc,Technology,1800000,75\n"
            ))

            connector = CSVConnector(
                directory=tmpdir,
                object_type="Customer",
                tenant_id="test-tenant",
            )
            connected = await connector.connect()
            assert connected is True

            mapping = {
                "Company Name": "name",
                "Industry": "industry",
                "Annual Revenue": "revenue",
                "Employees": "employees",
            }
            result = await connector.sync(onto, type_mapping=mapping)

            assert isinstance(result, SyncResult)
            assert result.objects_created == 3
            assert result.errors == []
            assert result.duration_seconds > 0

            # Verify objects in ontology
            customers = onto.query_objects("Customer")
            assert len(customers) == 3

            # Verify property mapping worked
            names = {c.properties["name"] for c in customers}
            assert names == {"Acme Corp", "GlobalTech", "DataFlow Inc"}

            # Verify numeric coercion
            acme = [c for c in customers if c.properties["name"] == "Acme Corp"][0]
            assert acme.properties["revenue"] == 5000000
            assert acme.properties["employees"] == 250

            # Verify source tracking
            assert acme.source == "csv:customers.csv"
            assert acme.tenant_id == "test-tenant"

            await connector.disconnect()

    async def test_csv_incremental_sync_skips_already_imported(self):
        """Second sync of same file should produce zero new objects."""
        onto = _make_ontology()

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(Path(tmpdir), "data.csv", (
                "Company Name,Industry\n"
                "Acme Corp,Technology\n"
            ))

            connector = CSVConnector(directory=tmpdir, object_type="Customer")
            await connector.connect()

            mapping = {"Company Name": "name", "Industry": "industry"}

            # First sync
            r1 = await connector.sync(onto, type_mapping=mapping)
            assert r1.objects_created == 1

            # Second sync of same file (unchanged content)
            r2 = await connector.sync(onto, type_mapping=mapping)
            assert r2.objects_created == 0  # skipped via hash tracking

            await connector.disconnect()

    async def test_csv_discover_schema(self):
        """discover_schema should return file names and column headers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(Path(tmpdir), "test.csv", "Name,Age,City\nAlice,30,NYC\n")

            connector = CSVConnector(directory=tmpdir)
            await connector.connect()

            schema = await connector.discover_schema()
            assert "test.csv" in schema["files"]
            assert schema["files"]["test.csv"]["columns"] == ["Name", "Age", "City"]

            await connector.disconnect()

    async def test_csv_connect_invalid_directory(self):
        """connect() should return False for a nonexistent directory."""
        connector = CSVConnector(directory="/nonexistent/path")
        connected = await connector.connect()
        assert connected is False

    async def test_csv_empty_directory(self):
        """sync() with no CSV files should return zero counts."""
        onto = _make_ontology()

        with tempfile.TemporaryDirectory() as tmpdir:
            connector = CSVConnector(directory=tmpdir, object_type="Customer")
            await connector.connect()

            result = await connector.sync(onto, type_mapping={})
            assert result.objects_created == 0
            assert result.errors == []

            await connector.disconnect()

    async def test_csv_multiple_files(self):
        """sync() should process all CSV files in the directory."""
        onto = _make_ontology()

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(Path(tmpdir), "file1.csv", "Name\nAlice\nBob\n")
            _write_csv(Path(tmpdir), "file2.csv", "Name\nCharlie\n")

            connector = CSVConnector(directory=tmpdir, object_type="Customer")
            await connector.connect()

            result = await connector.sync(onto, type_mapping={"Name": "name"})
            assert result.objects_created == 3

            await connector.disconnect()


# ---------------------------------------------------------------------------
# API Connector
# ---------------------------------------------------------------------------

class TestAPIConnector:
    async def test_api_connector_sync(self):
        """Simulated API data should be mapped and ingested correctly."""
        onto = _make_ontology()

        simulated = [
            {"company": "Acme Corp", "sector": "Technology", "arr": 5000000},
            {"company": "GlobalTech", "sector": "Finance", "arr": 3200000},
        ]

        connector = GenericAPIConnector(
            object_type="Customer",
            tenant_id="test-tenant",
            simulated_data=simulated,
        )
        connected = await connector.connect()
        assert connected is True

        mapping = {
            "company": "name",
            "sector": "industry",
            "arr": "revenue",
        }
        result = await connector.sync(onto, type_mapping=mapping)

        assert result.objects_created == 2
        assert result.errors == []

        customers = onto.query_objects("Customer")
        assert len(customers) == 2

        names = {c.properties["name"] for c in customers}
        assert names == {"Acme Corp", "GlobalTech"}

        # Verify numeric values pass through (no coercion needed — already int)
        acme = [c for c in customers if c.properties["name"] == "Acme Corp"][0]
        assert acme.properties["revenue"] == 5000000

        await connector.disconnect()

    async def test_api_connector_empty_data(self):
        """Empty simulated data should produce zero objects."""
        onto = _make_ontology()

        connector = GenericAPIConnector(
            object_type="Customer",
            simulated_data=[],
        )
        await connector.connect()

        result = await connector.sync(onto, type_mapping={})
        assert result.objects_created == 0
        assert result.errors == []

        await connector.disconnect()

    async def test_api_connector_discover_schema(self):
        """discover_schema should describe the endpoint config."""
        connector = GenericAPIConnector(
            url="https://api.example.com/data",
            response_path="data.results",
            object_type="Lead",
            simulated_data=[],
        )
        await connector.connect()

        schema = await connector.discover_schema()
        assert schema["url"] == "https://api.example.com/data"
        assert schema["response_path"] == "data.results"
        assert schema["object_type"] == "Lead"
        assert schema["simulated"] is True

        await connector.disconnect()

    async def test_api_connector_not_connected(self):
        """sync() without connect() should return an error."""
        onto = _make_ontology()
        connector = GenericAPIConnector(object_type="Customer")
        result = await connector.sync(onto, type_mapping={})
        assert len(result.errors) > 0
        assert "Not connected" in result.errors[0]


# ---------------------------------------------------------------------------
# Google Sheets Connector
# ---------------------------------------------------------------------------

class TestSheetsConnector:
    async def test_sheets_connector_simulated_sync(self):
        """Simulated sheets connector should create objects from sample data."""
        onto = _make_ontology()

        connector = GoogleSheetsConnector(
            object_type="Customer",
            tenant_id="test-tenant",
            simulated=True,
        )
        connected = await connector.connect()
        assert connected is True

        mapping = {
            "company_name": "name",
            "industry": "industry",
            "arr": "revenue",
            "employees": "employees",
        }
        result = await connector.sync(onto, type_mapping=mapping)

        assert result.objects_created == 5  # 5 sample rows
        assert result.errors == []

        customers = onto.query_objects("Customer")
        assert len(customers) == 5

        await connector.disconnect()

    async def test_sheets_discover_schema(self):
        """discover_schema should list columns from sample data."""
        connector = GoogleSheetsConnector(simulated=True)
        await connector.connect()

        schema = await connector.discover_schema()
        assert "columns" in schema
        assert "company_name" in schema["columns"]
        assert schema["row_count"] == 5
        assert schema["simulated"] is True

        await connector.disconnect()


# ---------------------------------------------------------------------------
# Database Connector
# ---------------------------------------------------------------------------

class TestDatabaseConnector:
    async def test_db_connector_simulated_sync(self):
        """Simulated DB connector should create objects from sample data."""
        onto = _make_ontology()

        simulated = [
            {"company_name": "Acme Corp", "industry": "Technology", "annual_revenue": 5000000},
            {"company_name": "GlobalTech", "industry": "Finance", "annual_revenue": 3200000},
        ]

        connector = DatabaseConnector(
            query="SELECT * FROM customers",
            object_type="Customer",
            tenant_id="test-tenant",
            simulated_data=simulated,
        )
        connected = await connector.connect()
        assert connected is True

        mapping = {
            "company_name": "name",
            "industry": "industry",
            "annual_revenue": "revenue",
        }
        result = await connector.sync(onto, type_mapping=mapping)

        assert result.objects_created == 2
        assert result.errors == []

        customers = onto.query_objects("Customer")
        assert len(customers) == 2

        await connector.disconnect()

    async def test_db_connector_no_connection(self):
        """connect() should return False with no db_client or simulated data."""
        connector = DatabaseConnector(query="SELECT 1")
        connected = await connector.connect()
        assert connected is False


# ---------------------------------------------------------------------------
# Sync Engine
# ---------------------------------------------------------------------------

class TestSyncEngine:
    async def test_sync_engine_runs_all(self):
        """sync_all() should run every registered connector."""
        onto = _make_ontology()

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_csv(Path(tmpdir), "data.csv", "Name,Industry\nAcme,Tech\nGlobal,Finance\n")

            csv_conn = CSVConnector(directory=tmpdir, object_type="Customer")
            api_conn = GenericAPIConnector(
                object_type="Customer",
                simulated_data=[{"co": "DataFlow", "ind": "SaaS"}],
            )

            engine = SyncEngine(
                ontology=onto,
                connectors=[csv_conn, api_conn],
                default_type_mapping={
                    "csv": {"Name": "name", "Industry": "industry"},
                    "api": {"co": "name", "ind": "industry"},
                },
            )

            results = await engine.sync_all()

            assert "csv" in results
            assert "api" in results
            assert results["csv"].objects_created == 2
            assert results["api"].objects_created == 1

            # Ontology should contain all 3 objects
            all_customers = onto.query_objects("Customer")
            assert len(all_customers) == 3

    async def test_sync_engine_sync_one(self):
        """sync_one() should run only the named connector."""
        onto = _make_ontology()

        api_conn = GenericAPIConnector(
            object_type="Customer",
            simulated_data=[{"co": "Acme"}],
        )
        sheets_conn = GoogleSheetsConnector(
            object_type="Customer",
            simulated=True,
        )

        engine = SyncEngine(
            ontology=onto,
            connectors=[api_conn, sheets_conn],
            default_type_mapping={
                "api": {"co": "name"},
                "google_sheets": {"company_name": "name"},
            },
        )

        result = await engine.sync_one("api")
        assert result.objects_created == 1

        # Sheets connector should not have run
        all_customers = onto.query_objects("Customer")
        assert len(all_customers) == 1

    async def test_sync_engine_unknown_connector(self):
        """sync_one() with unknown name should return error result."""
        onto = _make_ontology()
        engine = SyncEngine(ontology=onto, connectors=[])

        result = await engine.sync_one("nonexistent")
        assert len(result.errors) == 1
        assert "Unknown connector" in result.errors[0]

    async def test_sync_engine_connector_names(self):
        """connector_names should list all registered connectors."""
        onto = _make_ontology()
        csv_conn = CSVConnector(directory="/tmp", object_type="Customer")
        api_conn = GenericAPIConnector(object_type="Customer", simulated_data=[])

        engine = SyncEngine(ontology=onto, connectors=[csv_conn, api_conn])
        assert set(engine.connector_names) == {"csv", "api"}

    async def test_sync_engine_last_sync_results(self):
        """last_sync_results should be populated after sync_all()."""
        onto = _make_ontology()
        api_conn = GenericAPIConnector(
            object_type="Customer",
            simulated_data=[{"x": "y"}],
        )

        engine = SyncEngine(
            ontology=onto,
            connectors=[api_conn],
            default_type_mapping={"api": {"x": "name"}},
        )

        assert engine.last_sync_results == {}
        await engine.sync_all()
        assert "api" in engine.last_sync_results
        assert engine.last_sync_results["api"].objects_created == 1


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------

class TestSyncResult:
    def test_sync_result_counts(self):
        """SyncResult should track created, updated, links, and errors."""
        result = SyncResult(
            objects_created=10,
            objects_updated=3,
            links_created=5,
            errors=["bad row"],
            duration_seconds=1.5,
        )
        assert result.objects_created == 10
        assert result.objects_updated == 3
        assert result.links_created == 5
        assert result.total_objects == 13
        assert len(result.errors) == 1
        assert result.duration_seconds == 1.5

    def test_sync_result_defaults(self):
        """SyncResult defaults should all be zero/empty."""
        result = SyncResult()
        assert result.objects_created == 0
        assert result.objects_updated == 0
        assert result.links_created == 0
        assert result.errors == []
        assert result.duration_seconds == 0.0
        assert result.total_objects == 0

    def test_sync_result_repr(self):
        """repr should include all key counts."""
        result = SyncResult(objects_created=5, objects_updated=2, links_created=1)
        text = repr(result)
        assert "created=5" in text
        assert "updated=2" in text
        assert "links=1" in text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestCreateConnectorsFactory:
    def test_create_connectors_factory(self):
        """create_connectors should instantiate connectors from config dicts."""
        configs = [
            {"type": "csv", "directory": "/tmp", "object_type": "Customer"},
            {"type": "api", "url": "https://example.com", "object_type": "Lead"},
            {"type": "google_sheets", "simulated": True, "object_type": "Customer"},
            {"type": "database", "query": "SELECT 1", "object_type": "Record"},
        ]
        connectors = create_connectors(configs)

        assert len(connectors) == 4
        names = [c.name for c in connectors]
        assert "csv" in names
        assert "api" in names
        assert "google_sheets" in names
        assert "database" in names

    def test_create_connectors_empty(self):
        """create_connectors with empty list should return empty list."""
        connectors = create_connectors([])
        assert connectors == []

    def test_create_connectors_unknown_type(self):
        """Unknown connector types should be skipped (not raise)."""
        connectors = create_connectors([{"type": "salesforce"}])
        assert connectors == []

    def test_create_connectors_sheets_alias(self):
        """'sheets' should be accepted as alias for 'google_sheets'."""
        connectors = create_connectors([{"type": "sheets", "simulated": True}])
        assert len(connectors) == 1
        assert connectors[0].name == "google_sheets"

    def test_create_connectors_db_alias(self):
        """'db' should be accepted as alias for 'database'."""
        connectors = create_connectors([{"type": "db", "query": "SELECT 1"}])
        assert len(connectors) == 1
        assert connectors[0].name == "database"
