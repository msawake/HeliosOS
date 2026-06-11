"""Treasury mock data — synthetic, internally-consistent reconciliation datasets.

This is the stand-in for the real BigQuery finance warehouse while access is
pending. The tables here mirror the *shape* of what the reconciliation agents
will eventually read from BigQuery (SAP open items, bank inflows, debt
schedule, purchase orders, Kyriba cash positions, plus the mapping tables).

The data is HAND-CRAFTED, not random: every table contains clean matches AND
deliberately planted exceptions (amount mismatches, missing mappings,
unidentified receipts, maverick spend, cash drift, …) so the agents have real
work to do and the demo is reproducible byte-for-byte.

Reconciliation period: month-to-date June 2026 (`AS_OF`).

Usage:
    python -m src.companies.treasury.mock_data           # write CSVs to ./data
    TREASURY_MOCK_DATA_DIR=/tmp/t python -m ...mock_data  # custom output dir

The agents read these as CSV via `shell__exec` (`cat data/<table>.csv`). When
real BigQuery access lands, the only change is the data source: the same agent
swaps `cat data/*.csv` for `bq query` (already in the shell allowlist). No
prompt or logic change.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

AS_OF = "2026-06-09"
CURRENCY = "EUR"

# ---------------------------------------------------------------------------
# Mapping tables (owned by the Mapping & Classification agent)
# ---------------------------------------------------------------------------

# Bank statements name counterparties freely; SAP keys everything by customer
# id. This table is the bridge. Two real-world counterparties below are
# DELIBERATELY ABSENT (see bank_inflows) → classification exceptions.
CUSTOMER_MAPPING = (
    ["bank_counterparty", "sap_customer_id", "sap_customer_name"],
    [
        ["ACME RETAIL GROUP",            "C1001", "Acme Retail Group SL"],
        ["ACME RETAIL GRP",              "C1001", "Acme Retail Group SL"],   # alias
        ["BLUEWAVE LOGISTICS",           "C1002", "Bluewave Logistics SA"],
        ["NORDIC FOODS AB",              "C1003", "Nordic Foods AB"],
        ["MERIDIAN MEDIA",               "C1004", "Meridian Media Ltd"],
        ["ORION PHARMA",                 "C1005", "Orion Pharma GmbH"],
        ["SUNPEAK ENERGY",               "C1006", "Sunpeak Energy SL"],
        ["VERTEX CONSULTING",            "C1007", "Vertex Consulting SA"],
    ],
)

VENDOR_MAPPING = (
    ["vendor_alias", "sap_vendor_id", "sap_vendor_name"],
    [
        ["OFFICEPRO SUPPLIES", "V2001", "OfficePro Supplies SL"],
        ["CLOUDSCALE HOSTING",  "V2002", "CloudScale Hosting Ltd"],
        ["TRAVELWISE",          "V2003", "TravelWise SA"],
        ["LEXBRIDGE LEGAL",     "V2004", "Lexbridge Legal LLP"],
        ["GREENFLEET LEASING",  "V2005", "GreenFleet Leasing GmbH"],
    ],
)

GL_ACCOUNT_MAPPING = (
    ["category", "gl_account", "description"],
    [
        ["customer_receipt",  "411000", "Trade receivables - clearing"],
        ["debt_principal",    "511000", "Loans payable - principal"],
        ["debt_interest",     "662000", "Interest expense"],
        ["vendor_payment",    "401000", "Trade payables - clearing"],
        ["bank_fee",          "627000", "Bank charges"],
        ["unidentified",      "199999", "Suspense - unidentified receipts"],
    ],
)

# ---------------------------------------------------------------------------
# 1. Bank inflows ↔ SAP reconciliation  (flagship agent)
# ---------------------------------------------------------------------------

# SAP open AR items — what finance expects to collect.
SAP_OPEN_ITEMS = (
    ["sap_doc_id", "customer_id", "customer_name", "invoice_ref", "amount_eur", "due_date", "status"],
    [
        ["SAP-90011", "C1001", "Acme Retail Group SL", "INV-5567", "12450.00", "2026-06-05", "open"],
        ["SAP-90012", "C1002", "Bluewave Logistics SA", "INV-5571", "8800.00",  "2026-06-06", "open"],
        ["SAP-90013", "C1003", "Nordic Foods AB",       "INV-5572", "23100.00", "2026-06-07", "open"],
        ["SAP-90014", "C1004", "Meridian Media Ltd",    "INV-5580", "4150.50",  "2026-06-08", "open"],
        ["SAP-90015", "C1005", "Orion Pharma GmbH",     "INV-5583", "61000.00", "2026-06-08", "open"],
        ["SAP-90016", "C1006", "Sunpeak Energy SL",     "INV-5590", "9750.00",  "2026-06-09", "open"],
        ["SAP-90017", "C1007", "Vertex Consulting SA",  "INV-5592", "15200.00", "2026-06-10", "open"],
        # Items with NO matching bank inflow yet → still outstanding (expected open).
        ["SAP-90018", "C1002", "Bluewave Logistics SA", "INV-5595", "3300.00",  "2026-06-11", "open"],
        ["SAP-90019", "C1003", "Nordic Foods AB",       "INV-5599", "18900.00", "2026-06-12", "open"],
    ],
)

# Bank statement inflows — what actually arrived.
BANK_INFLOWS = (
    ["bank_txn_id", "value_date", "amount_eur", "currency", "counterparty_name", "bank_reference", "account_iban"],
    [
        # --- clean matches (mapped counterparty + exact amount) ---
        ["BNK-7001", "2026-06-05", "12450.00", "EUR", "ACME RETAIL GROUP",  "INV-5567",       "ES7621000418450200051332"],
        ["BNK-7002", "2026-06-06", "8800.00",  "EUR", "BLUEWAVE LOGISTICS", "INV5571",        "ES7621000418450200051332"],
        ["BNK-7003", "2026-06-07", "23100.00", "EUR", "NORDIC FOODS AB",    "INV-5572 PAYMENT","ES7621000418450200051332"],
        ["BNK-7004", "2026-06-09", "61000.00", "EUR", "ORION PHARMA",       "INV-5583",       "ES7621000418450200051332"],
        # --- amount mismatch: short payment (3.50 less, likely cross-border fee) ---
        ["BNK-7005", "2026-06-08", "4147.00",  "EUR", "MERIDIAN MEDIA",     "INV-5580",       "ES7621000418450200051332"],
        # --- amount mismatch: overpayment / merged two invoices ---
        ["BNK-7006", "2026-06-09", "13050.00", "EUR", "SUNPEAK ENERGY",     "MULTIPLE",       "ES7621000418450200051332"],
        # --- counterparty NOT in customer_mapping → classification exception ---
        ["BNK-7007", "2026-06-09", "5400.00",  "EUR", "ZENITH HOLDINGS",    "REF 884412",     "ES7621000418450200051332"],
        # --- alias match (ACME RETAIL GRP maps via alias row) ---
        ["BNK-7008", "2026-06-10", "2750.00",  "EUR", "ACME RETAIL GRP",    "INV-5601",       "ES7621000418450200051332"],
        # --- unidentified receipt: no ref, counterparty unmapped, no SAP item ---
        ["BNK-7009", "2026-06-10", "1999.99",  "EUR", "PAYPAL EUROPE",      "TRANSFER",       "ES7621000418450200051332"],
        # --- Vertex paid via the mapped name, exact ---
        ["BNK-7010", "2026-06-10", "15200.00", "EUR", "VERTEX CONSULTING",  "INV-5592",       "ES7621000418450200051332"],
        # --- duplicate of BNK-7001 (same ref/amount, next day) → possible double-post ---
        ["BNK-7011", "2026-06-06", "12450.00", "EUR", "ACME RETAIL GROUP",  "INV-5567",       "ES7621000418450200051332"],
    ],
)

# ---------------------------------------------------------------------------
# 2. Debt reconciliation
# ---------------------------------------------------------------------------

DEBT_INSTRUMENTS = (
    ["loan_id", "lender", "principal_eur", "rate_pct", "start_date", "maturity_date", "currency"],
    [
        ["LN-01", "Banco Santander",  "2000000.00", "4.25", "2024-01-15", "2029-01-15", "EUR"],
        ["LN-02", "BBVA",             "750000.00",  "3.90", "2025-03-01", "2028-03-01", "EUR"],
        ["LN-03", "EIB Green Credit", "1200000.00", "2.75", "2025-09-01", "2032-09-01", "EUR"],
    ],
)

# Scheduled service due in the period.
DEBT_SCHEDULE = (
    ["loan_id", "due_date", "scheduled_principal_eur", "scheduled_interest_eur"],
    [
        ["LN-01", "2026-06-15", "33333.33", "7083.33"],
        ["LN-02", "2026-06-01", "20833.33", "2437.50"],
        ["LN-03", "2026-06-01", "0.00",     "2750.00"],
    ],
)

# Actual payments seen on the bank / Kyriba side.
DEBT_PAYMENTS = (
    ["payment_id", "loan_id", "pay_date", "amount_eur"],
    [
        # LN-01: paid in full (principal + interest combined) — clean.
        ["DPAY-01", "LN-01", "2026-06-15", "40416.66"],
        # LN-02: interest only paid, principal MISSED → exception.
        ["DPAY-02", "LN-02", "2026-06-01", "2437.50"],
        # LN-03: paid, but 250.00 MORE than scheduled → overpayment exception.
        ["DPAY-03", "LN-03", "2026-06-01", "3000.00"],
        # Unmatched payment: references a loan id that doesn't exist → exception.
        ["DPAY-04", "LN-09", "2026-06-03", "5000.00"],
    ],
)

# ---------------------------------------------------------------------------
# 3. PO reconciliation (3-way match: PO ↔ invoice ↔ payment)
# ---------------------------------------------------------------------------

PURCHASE_ORDERS = (
    ["po_number", "vendor_id", "vendor_name", "po_amount_eur", "currency", "po_date", "status"],
    [
        ["PO-3001", "V2001", "OfficePro Supplies SL", "4200.00",  "EUR", "2026-05-20", "approved"],
        ["PO-3002", "V2002", "CloudScale Hosting Ltd", "18000.00", "EUR", "2026-05-22", "approved"],
        ["PO-3003", "V2003", "TravelWise SA",          "6500.00",  "EUR", "2026-05-25", "approved"],
        ["PO-3004", "V2005", "GreenFleet Leasing GmbH","12000.00", "EUR", "2026-05-28", "approved"],
        # PO with NO invoice yet → open commitment (expected).
        ["PO-3005", "V2001", "OfficePro Supplies SL", "990.00",   "EUR", "2026-06-02", "approved"],
    ],
)

VENDOR_INVOICES = (
    ["invoice_id", "po_number", "vendor_id", "invoice_amount_eur", "invoice_date"],
    [
        ["VINV-8001", "PO-3001", "V2001", "4200.00",  "2026-06-01"],   # clean
        ["VINV-8002", "PO-3002", "V2002", "19500.00", "2026-06-03"],   # OVER PO by 1500 → exception
        ["VINV-8003", "PO-3003", "V2003", "6500.00",  "2026-06-04"],   # clean
        ["VINV-8004", "PO-3001", "V2001", "4200.00",  "2026-06-05"],   # DUPLICATE invoice vs 8001 → exception
        # Invoice with NO purchase order → maverick / off-contract spend → exception.
        ["VINV-8005", "",        "V2004", "3100.00",  "2026-06-05"],
    ],
)

AP_PAYMENTS = (
    ["payment_id", "invoice_id", "po_number", "pay_date", "amount_eur"],
    [
        ["APAY-01", "VINV-8001", "PO-3001", "2026-06-08", "4200.00"],  # clean
        ["APAY-02", "VINV-8003", "PO-3003", "2026-06-09", "6500.00"],  # clean
        # VINV-8002 not yet paid (sits as exception until over-bill resolved).
    ],
)

# ---------------------------------------------------------------------------
# 4. Cash positions — Kyriba export vs BigQuery mirror (Quality agent, Phase 2)
# ---------------------------------------------------------------------------

KYRIBA_CASH_POSITIONS = (
    ["as_of_date", "account", "bank", "currency", "closing_balance_eur"],
    [
        ["2026-06-09", "ES76-MAIN-OPS",  "Santander", "EUR", "1840250.75"],
        ["2026-06-09", "ES21-PAYROLL",   "BBVA",      "EUR", "320000.00"],
        ["2026-06-09", "DE89-EU-COLLECT","Deutsche",  "EUR", "905120.40"],
        ["2026-06-09", "GB29-UK-COLLECT","Barclays",  "EUR", "412300.00"],
    ],
)

# The "BigQuery MS" mirror finance reports off — should equal Kyriba.
BQ_CASH_MIRROR = (
    ["as_of_date", "account", "currency", "closing_balance_eur"],
    [
        ["2026-06-09", "ES76-MAIN-OPS",  "EUR", "1840250.75"],   # match
        ["2026-06-09", "ES21-PAYROLL",   "EUR", "320000.00"],    # match
        ["2026-06-09", "DE89-EU-COLLECT","EUR", "904120.40"],    # DRIFT: 1000.00 lower → exception
        # GB29-UK-COLLECT MISSING from BQ mirror entirely → exception
    ],
)

# ---------------------------------------------------------------------------
# Registry + writer
# ---------------------------------------------------------------------------

TABLES: dict[str, tuple[list[str], list[list[str]]]] = {
    "customer_mapping": CUSTOMER_MAPPING,
    "vendor_mapping": VENDOR_MAPPING,
    "gl_account_mapping": GL_ACCOUNT_MAPPING,
    "sap_open_items": SAP_OPEN_ITEMS,
    "bank_inflows": BANK_INFLOWS,
    "debt_instruments": DEBT_INSTRUMENTS,
    "debt_schedule": DEBT_SCHEDULE,
    "debt_payments": DEBT_PAYMENTS,
    "purchase_orders": PURCHASE_ORDERS,
    "vendor_invoices": VENDOR_INVOICES,
    "ap_payments": AP_PAYMENTS,
    "kyriba_cash_positions": KYRIBA_CASH_POSITIONS,
    "bq_cash_mirror": BQ_CASH_MIRROR,
}


def default_data_dir() -> Path:
    """Where CSVs are written/read. Override with TREASURY_MOCK_DATA_DIR."""
    env = os.environ.get("TREASURY_MOCK_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "data"


def write_csvs(data_dir: Path | None = None) -> Path:
    """Write every table to <data_dir>/<table>.csv. Returns the directory."""
    out = data_dir or default_data_dir()
    out.mkdir(parents=True, exist_ok=True)
    for name, (columns, rows) in TABLES.items():
        with open(out / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(rows)
    return out


def summary() -> str:
    lines = [f"Treasury mock data (as of {AS_OF}, {CURRENCY}) — {len(TABLES)} tables:"]
    for name, (_, rows) in TABLES.items():
        lines.append(f"  - {name:<22} {len(rows):>3} rows")
    return "\n".join(lines)


if __name__ == "__main__":
    out = write_csvs()
    print(summary())
    print(f"\nWrote CSVs to: {out}")
