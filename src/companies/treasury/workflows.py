"""Treasury workflows — stubs for platform boot.

Reconciliation orchestration is expressed through the Kyriba Chat Orchestrator
agent (A2A fan-out to the reconciliation agents), not through these legacy
workflow builders. Stubs exist to satisfy the company-pack loader."""


def _stub_workflow(*args, **kwargs):
    return {"status": "stub", "steps": []}


create_reconciliation_workflow = _stub_workflow
create_cash_quality_workflow = _stub_workflow
create_collections_workflow = _stub_workflow
