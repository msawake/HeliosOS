"""Treasury knowledge base — reconciliation policy, classification rules,
approval policy and the data dictionary the agents reason against.

Seeded at boot (src/bootstrap.py Phase 5). Agents retrieve these via
company__search_knowledge / knowledge__search instead of hard-coding rules in
prompts, so policy can change without redeploying agents."""

from __future__ import annotations


def seed_knowledge_base(kb):
    """Seed the Treasury knowledge base."""

    kb.add(
        category="policy",
        title="Reconciliation Matching Rules",
        content=(
            "Match tolerance: two amounts reconcile if they differ by no more than "
            "the GREATER of EUR 5.00 or 0.5%. A difference within tolerance is logged "
            "as a fee/short-payment, not an exception. "
            "Bank inflow ↔ SAP open item: match on (mapped customer id) + amount within "
            "tolerance; use invoice reference when present to disambiguate. "
            "Value/posting dates may differ by up to 3 business days (settlement lag). "
            "A bank reference and invoice_ref are considered equal ignoring spaces, "
            "case and the 'INV' prefix punctuation (e.g. 'INV5571' == 'INV-5571')."
        ),
        tags=["reconciliation", "matching", "tolerance"],
        created_by="system",
        department="treasury",
    )

    kb.add(
        category="policy",
        title="Reconciliation Exception Taxonomy",
        content=(
            "Every unmatched or anomalous item is classified as one of: "
            "UNMAPPED_COUNTERPARTY (bank name absent from customer/vendor mapping); "
            "AMOUNT_MISMATCH (difference beyond tolerance); "
            "UNIDENTIFIED_RECEIPT (inflow with no SAP item and no mapping → suspense 199999); "
            "MISSED_PAYMENT (scheduled debt/AP due but not paid); "
            "OVERPAYMENT (paid more than scheduled/invoiced); "
            "DUPLICATE (same reference + amount seen twice); "
            "MAVERICK_SPEND (vendor invoice with no purchase order); "
            "PO_OVERBILL (invoice amount exceeds PO); "
            "CASH_DRIFT (Kyriba vs BigQuery balance differs); "
            "MISSING_IN_MIRROR (account in Kyriba absent from BigQuery). "
            "Each exception names the source rows, the EUR delta, and a proposed action."
        ),
        tags=["reconciliation", "exceptions", "classification"],
        created_by="system",
        department="treasury",
    )

    kb.add(
        category="policy",
        title="GL Classification Rules",
        content=(
            "Classify reconciled/exception items to a GL account using gl_account_mapping: "
            "customer_receipt→411000, debt_principal→511000, debt_interest→662000, "
            "vendor_payment→401000, bank_fee→627000, unidentified→199999 (suspense). "
            "Short-payment differences attributable to cross-border charges post to 627000. "
            "Unidentified receipts park in 199999 until a counterparty mapping is added."
        ),
        tags=["classification", "gl", "mapping"],
        created_by="system",
        department="treasury",
    )

    kb.add(
        category="policy",
        title="Treasury Approval Policy",
        content=(
            "PROTOTYPE phase: the agents are READ-ONLY analysts. They produce "
            "reconciliation reports and exception lists; they do NOT post to SAP, "
            "release payments, or send external email autonomously. "
            "Any action that would mutate a system of record or contact a "
            "counterparty requires human approval via human__ask, routed to the "
            "Treasury approver (antoni.bergas@makingscience.com). "
            "Exceptions with EUR delta over 10,000 are escalated for review even "
            "when no action is proposed."
        ),
        tags=["approval", "hitl", "governance"],
        created_by="system",
        department="treasury",
    )

    kb.add(
        category="technical",
        title="Treasury Data Dictionary (mock + future BigQuery)",
        content=(
            "Source tables (CSV at src/companies/treasury/data/ today; BigQuery later): "
            "customer_mapping(bank_counterparty→sap_customer_id); "
            "vendor_mapping(vendor_alias→sap_vendor_id); gl_account_mapping(category→gl_account); "
            "sap_open_items(sap_doc_id, customer_id, invoice_ref, amount_eur, due_date, status); "
            "bank_inflows(bank_txn_id, value_date, amount_eur, counterparty_name, bank_reference); "
            "debt_instruments / debt_schedule(loan_id, due_date, scheduled_principal/interest) / "
            "debt_payments(payment_id, loan_id, amount_eur); "
            "purchase_orders(po_number, vendor_id, po_amount_eur) / vendor_invoices(invoice_id, "
            "po_number, invoice_amount_eur) / ap_payments(payment_id, invoice_id, amount_eur); "
            "kyriba_cash_positions(account, closing_balance_eur) vs bq_cash_mirror(account, "
            "closing_balance_eur). All amounts EUR. Reconciliation period as-of 2026-06-09."
        ),
        tags=["data dictionary", "schema", "bigquery", "mock data"],
        created_by="system",
        department="treasury",
    )
