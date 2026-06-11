"""Treasury company pack — manifests validate, mock data is consistent, and the
deliberately planted reconciliation exceptions are present.

These guard the mock-data prototype: if someone regenerates the data or edits a
manifest into an invalid shape, CI catches it before a deploy."""

from __future__ import annotations

import csv
import glob
import pathlib

import pytest

from src.forgeos_sdk.manifest import AgentManifest

PACK = pathlib.Path(__file__).resolve().parents[1] / "src" / "companies" / "treasury"
AGENT_FILES = sorted(glob.glob(str(PACK / "agents" / "*.yaml")))

EXPECTED_AGENTS = {
    "kyriba-chat-orchestrator",
    "bank-sap-reconciliation",
    "debt-reconciliation",
    "po-reconciliation",
    "mapping-classification",
}


# --------------------------------------------------------------------------- #
# Manifests
# --------------------------------------------------------------------------- #

def test_all_five_agents_present():
    names = {AgentManifest.from_yaml(f).metadata.name for f in AGENT_FILES}
    assert names == EXPECTED_AGENTS


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: pathlib.Path(p).stem)
def test_manifest_validates(path):
    m = AgentManifest.from_yaml(path)
    assert m.metadata.department == "treasury"
    assert m.spec.tools, "agent declares at least one tool"
    # scheduled agents must carry a cron (schema enforces, assert intent here)
    if m.spec.execution_type == "scheduled":
        assert m.spec.schedule


def test_company_pack_loads():
    from src.config.agent_configs import (
        load_company_config,
        load_company_demo,
        load_company_knowledge,
        load_company_module,
    )

    cfg = load_company_config(company_id="treasury")
    assert cfg["company"]["name"] == "Treasury"
    assert load_company_module("treasury").build_registry() is not None
    assert callable(load_company_knowledge("treasury").seed_knowledge_base)
    assert callable(load_company_demo("treasury").run_demo)


# --------------------------------------------------------------------------- #
# Mock data
# --------------------------------------------------------------------------- #

def test_mock_data_writes_all_tables(tmp_path):
    from src.companies.treasury import mock_data

    out = mock_data.write_csvs(tmp_path)
    for name in mock_data.TABLES:
        f = out / f"{name}.csv"
        assert f.exists(), f"{name}.csv written"
        rows = list(csv.DictReader(f.open()))
        assert rows, f"{name} has rows"


def _rows(tmp_path, table):
    from src.companies.treasury import mock_data

    mock_data.write_csvs(tmp_path)
    return list(csv.DictReader((tmp_path / f"{table}.csv").open()))


def test_planted_exception_unmapped_counterparty(tmp_path):
    """ZENITH HOLDINGS arrives in the bank but is absent from customer_mapping."""
    inflows = _rows(tmp_path, "bank_inflows")
    mapping = _rows(tmp_path, "customer_mapping")
    counterparties = {r["counterparty_name"] for r in inflows}
    mapped = {r["bank_counterparty"] for r in mapping}
    assert "ZENITH HOLDINGS" in counterparties
    assert "ZENITH HOLDINGS" not in mapped


def test_planted_exception_cash_drift(tmp_path):
    """DE89 balance drifts and GB29 is missing from the BigQuery mirror."""
    kyriba = {r["account"]: r["closing_balance_eur"] for r in _rows(tmp_path, "kyriba_cash_positions")}
    mirror = {r["account"]: r["closing_balance_eur"] for r in _rows(tmp_path, "bq_cash_mirror")}
    assert kyriba["DE89-EU-COLLECT"] != mirror["DE89-EU-COLLECT"]  # drift
    assert "GB29-UK-COLLECT" in kyriba and "GB29-UK-COLLECT" not in mirror  # missing


def test_planted_exception_po_anomalies(tmp_path):
    """A duplicate invoice, a maverick (no-PO) invoice, and a PO over-bill exist."""
    invoices = _rows(tmp_path, "vendor_invoices")
    pos = {r["po_number"]: float(r["po_amount_eur"]) for r in _rows(tmp_path, "purchase_orders")}
    # duplicate: same po_number + amount appears twice
    seen, dupes = set(), 0
    for r in invoices:
        key = (r["po_number"], r["invoice_amount_eur"])
        if key in seen and r["po_number"]:
            dupes += 1
        seen.add(key)
    assert dupes >= 1
    # maverick: an invoice with no po_number
    assert any(not r["po_number"] for r in invoices)
    # over-bill: an invoice exceeds its PO beyond a trivial tolerance
    assert any(r["po_number"] in pos and float(r["invoice_amount_eur"]) > pos[r["po_number"]] + 5
               for r in invoices)


def test_planted_exception_debt(tmp_path):
    """A debt payment references a loan id that doesn't exist."""
    loans = {r["loan_id"] for r in _rows(tmp_path, "debt_instruments")}
    payments = {r["loan_id"] for r in _rows(tmp_path, "debt_payments")}
    assert payments - loans, "at least one payment points at an unknown loan"
