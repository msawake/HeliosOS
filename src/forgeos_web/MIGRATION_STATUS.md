# FastAPI → Django migration — status & cutover runbook

Full plan: `~/.claude/plans/backend-must-be-migrated-vectorized-meadow.md`.

**Replace-in-place (cutover executed).** The legacy FastAPI app
(`src/dashboard/fastapi_app.py`), the LLM streaming methods
(`chat_stream`/`_stream_*`), `run_agentic_loop_with_events`, and the orphaned
FastAPI integration tests have been **deleted**. `src/bootstrap.py` now serves the
**Django ASGI app** (`run_django_server`/`start_django_server`); platform singletons
are injected via the process-global `di.AppContext`. The Dockerfile installs the
`[django]` extra and docker-compose adds `worker` + `beat` services.

What remains before this is fully production-live: the **schema-ownership flip**
(models are still `managed=False`, which works at runtime against the existing
tables — flip to `managed=True` + `migrate --fake-initial` only when Django should
own migrations), and `/ws/agents` (Channels). See below.

## Status: all 11 steps code-complete & verified (Python 3.11 venv)

| Step | What | Verified by |
|---|---|---|
| 1 | Scaffold, ASGI, settings, `di` shim, **parity harness** | `manage.py check`, route extractor |
| 2 | ~40 ORM models (`managed=False`) + RLS + pgvector | check, makemigrations --dry-run, tenant-scoping test |
| 3 | DRF auth (reuses `src/api/auth.py` crypto) | 4 auth tests (token/401/403/200) |
| 4 | RBAC in Django admin (Groups, capabilities, shadow User) | migrate seeds Groups, 2 tests |
| 5 | Health + read-only apps + middleware + `{"detail"}` handler | check, parity |
| 6 | Celery (Redis) + async bridge + per-worker RuntimeService | app builds, 5 tasks, routes |
| 7 | All write/RPC apps (agents, kernel, mcps, clients, …) | **route parity 126/127, no drift** |
| 8 | Chat/SSE app; Django path has **zero** streaming-LLM dep | parity, no `.chat_stream(` calls |
| 9 | APScheduler → Celery Beat (`PeriodicTask` bridge) | cron-parse tests + DB register/list |
| 10 | RLS-DDL migration (Phase C) + fake-initial runbook | migrate apply+reverse (no-op on sqlite) |
| 11 | Cutover wiring (`run_django_server`, eviction Beat task) | check, bootstrap compiles, celery beat |

**Tests:** `tests/web/` (auth, RBAC, scheduling) + `tests/contract/` (route parity) — 11 passing.
**Route coverage:** 126/127 (99%). The one gap is `/ws/agents` (WebSocket → needs Django Channels).

## Resume / verification in the full env

```bash
pip install -e ".[django,production]"
PYTHONPATH=. python tests/contract/extract_fastapi_routes_static.py   # or snapshot_fastapi.py for full OpenAPI
docker compose up -d postgres redis
PYTHONPATH=. python src/forgeos_web/manage.py check
PYTHONPATH=. python -m pytest tests/web tests/contract -v
```

## Cutover runbook (supervised, live env — NOT yet executed)

These are the irreversible / live-only switches. Do them with Postgres+Redis up and
the contract suite green.

1. **Adopt the schema (Phase B).** Flip every domain model `managed = False` → `True`
   (apps: tenancy, eventbus, clients, agents, hitl, ontology, secrets, environments,
   runtime, governance), then:
   ```bash
   manage.py makemigrations          # generates 0001_initial per app (final column set)
   manage.py migrate --fake-initial  # adopts existing tables WITHOUT recreating them
   ```
   Then `manage.py migrate forgeos_rls` (or `--fake` it — policies already exist on prod).
   Freeze `src/core/migrations.py` + `infrastructure/database/*.sql` (read-only history).

2. **Serve Django instead of FastAPI.** In the boot entrypoint, call
   `await boot.run_django_server(...)` instead of `run_api_server(...)`
   (both exist in `src/bootstrap.py`; the Django one installs the `di.AppContext`).

3. **Run the worker tier.** Celery worker + beat processes; each worker must install
   the context at startup (boot the platform, call `boot.populate_web_context()` in
   `worker_process_init`):
   ```bash
   celery -A src.celery_app worker -Q agents,agents_resume,scheduled,agents_longrun
   celery -A src.celery_app beat --scheduler django_celery_beat.schedulers:DatabaseScheduler
   ```

4. **Dead code deletion — DONE in this PR.** `LLMRouter.chat_stream`/`_stream_*`,
   `run_agentic_loop_with_events`, `src/dashboard/fastapi_app.py`, and the orphaned
   FastAPI tests are removed. `bootstrap.py` serves Django.

5. **Deploy — DONE for docker.** Dockerfile installs `.[production,scheduler,django]`;
   docker-compose runs `app` (Django ASGI) + `worker` + `beat`. Still TODO for
   `pulumi/` (Cloud Run/GKE): add worker + beat deployments and ensure the image
   ships the django extra (it now does).

## Known remaining gaps (tracked)
- **`/ws/agents`** WebSocket — needs Django Channels (ASGI consumer + routing + channel
  layer). The 126 HTTP routes are ported; this 1 WS route is the only unported path.
- **In-process state moved to TODO(shared-store):** chat sessions
  (`chat/views.py:_chat_sessions`), admin sessions, intelligence sessions — currently
  per-process dicts; move to Redis so eviction (the `forgeos.evict_stale_sessions` Beat
  task) works cross-process.
- **`_audit` stubs:** ported views log audit events; wire the real `src.platform.audit`
  sink at cutover.
- **`/invoke` still inline:** ported faithfully with `async_to_sync(...invoke)` + a
  `TODO(step7-enqueue)`; flip to `run_agent.delay` + poll once the worker tier is live.
- **RLS under ATOMIC_REQUESTS:** the auth class sets `app.current_tenant` inside the
  request txn; validate the transaction-boundary behavior against live Postgres (and
  exempt the SSE views from ATOMIC_REQUESTS, using `tenant_context()` per DB touch).

## Conventions (for any further endpoint work)
APIView per path; hand-written `urlpatterns` (paths byte-identical); response bodies are
raw dicts (the contract); `require_role(...)`/`has_capability(...)` only where FastAPI
gated; platform objects from `di.get_context()`. Re-run
`tests/contract/test_route_parity.py::test_no_path_drift` after any change.
