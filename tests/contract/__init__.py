"""Contract-parity harness for the FastAPI -> Django migration.

The single regression net for the whole migration: the new Django app must serve
the same URL paths + methods (and, at runtime, the same JSON shapes) as the
legacy FastAPI app. `snapshot_fastapi.py` captures the FastAPI contract once;
`test_route_parity.py` diffs the live Django urlconf against it.
"""
