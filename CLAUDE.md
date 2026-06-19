# CLAUDE.md — ForgeOS / Helios OS

Agentic governance platform: run AI agents on any framework (ForgeOS-native, CrewAI,
ADK, OpenClaw, sandbox) under a policy kernel, with multi-tenant isolation, HITL
approvals, and a durable execution runtime.

The web/API layer was **migrated from FastAPI to Django + Django REST Framework**.
The legacy FastAPI app has been removed — Django is the only web layer. The agent
platform itself remains a library under `src/` that the Django views call into.

## Repository layout

```
/manage.py                  Django management entry point (run from repo root)
/forgeos_web/               THE DJANGO PROJECT (DRF API + admin + Celery)
    settings.py urls.py asgi.py        project config
    celery_app.py celery_runtime.py tasks.py   Celery (broker=Redis) + async bridge
    scheduling.py            APScheduler→Celery Beat PeriodicTask bridge
    di.py                    process-global AppContext (platform singletons)
    db/                      RLS: set_config tenant var, TenantModel/TenantManager, middleware
    authn/                   DRF auth + RBAC permission classes (reuses src/api/auth.py crypto)
    rbac/                    Django-admin RBAC surface + auto-register-all-models admin
    common/                  SecurityHeadersMiddleware, {"detail":...} exception handler
    <domain apps>            tenancy eventbus clients agents hitl ontology usercreds
                             environments runtime governance + view-only apps
                             (health auth_app approvals mcps kernel namespaces credentials
                              admin_app intelligence billing audit_events sandbox chat)
    rls_policies/            migration that recreates RLS policies on a fresh DB
/src/                       PLATFORM LIBRARY (imported by the Django apps as src.*)
    bootstrap.py             boots the platform (7 phases) then serves Django ASGI
    platform/                executor, kernel, registry, scheduler, llm_router, ...
    runtime/                 durable continuation engine (queue, ledger, StepEngine, resume)
    api/auth.py              framework-agnostic crypto + AuthManager (tokens/PBKDF2/API keys)
    core/                    DatabaseClient (raw psycopg + RLS), migrations runner
/stacks/                    agent stack adapters (forgeos, crewai, adk, openclaw, sandbox)
/a2h/                       agent-to-human protocol
/dashboard/                 Next.js frontend (talks to the API over the /api contract)
/infrastructure/database/   *.sql schema (the source of truth for the ORM models — frozen)
/tests/web/ /tests/contract/  Django-layer tests + route-parity gate
```

## Running the backend

The backend = boot the platform, then serve the Django ASGI app (uvicorn). It is NOT
`manage.py runserver` (that would start Django with an empty `di.AppContext`).

```bash
# Postgres (pgvector) + Redis must be up (docker-compose has them).
DATABASE_URL=postgresql://USER:PW@HOST:5433/DB \
REDIS_URL=redis://HOST:6379/0 \
VLLM_BASE_URL=https://atlas-router.ally-code-dev.makingscience.com/v1 \
VLLM_API_KEY=<gateway key> \
PYTHONPATH=.:a2h python -m src.bootstrap --no-auth --dashboard --port 5000
```
- `bootstrap.run_django_server()` installs `di.AppContext` from the live boot, then
  serves `forgeos_web.asgi:application`. `start_django_server()` is the threaded variant
  (used with `--loop`).
- Full stack: `docker compose up` (postgres, redis, `app` = Django, `worker`, `beat`,
  `dashboard`). The image installs the `[django]` extra.
- Celery: `celery -A forgeos_web.celery_app worker -Q agents,agents_resume,scheduled,agents_longrun`
  and `celery -A forgeos_web.celery_app beat --scheduler django_celery_beat.schedulers:DatabaseScheduler`.
- Management: `python manage.py {check,migrate,createsuperuser,...}` from the repo root.
- Django admin: `/admin/` (run `migrate` + `createsuperuser` first; `DJANGO_DEBUG=1`
  serves admin static under uvicorn).

## Architecture essentials

- **DI seam**: views/tasks read platform singletons from `forgeos_web.di.get_context()`
  (`AppContext`: executor, kernel, llm_router, db_client, platform_registry, runtime_service,
  mcp_manager, tool_executor, credential_store, …). Populated once at boot via
  `di.populate_from_bootstrap(boot)`.
- **Multi-tenancy / RLS**: DB-enforced Row-Level Security stays the real boundary.
  `db/rls.py` sets `app.current_tenant` via `set_config(..., is_local=true)`; `TenantModel`
  + `TenantManager` add ORM-level scoping (`all_objects` bypasses). RLS only enforces for a
  **non-superuser** DB role. `hitl_approvals` keys on `company_id` (override `TENANT_FIELD`);
  `agent_runs`/`capability_tokens`/`execution_workers` are non-RLS.
- **Auth + RBAC**: `authn/` reuses `src/api/auth.py` (so existing signed tokens + stored
  hashes validate unchanged). `ForgeOSAuthentication` (Bearer/JWT/API-key) + DRF permissions
  `IsAuthenticatedOrPublicPath`, `require_role(*roles)`, `has_capability(cap)`. The gate
  honors `--no-auth` via `di` (not just the env var). RBAC roles = Django Groups
  (admin/operator/viewer) seeded by `rbac/migrations`; managed in Django admin.
- **Celery hybrid**: Celery (on Redis) is the outer control plane (submit/Beat/supervision);
  the inner `src/runtime/` durable engine (RedisRunnableQueue + PostgresLedger + StepEngine)
  drives turns. Tasks are thin (`run_agent`/`resume_agent`/`scheduled_tick` over
  `RuntimeService.enqueue_invoke`). HITL and long-running tools are modeled as suspensions.
  Async-in-Celery via one long-lived event loop per worker (`celery_runtime.run_async`).
- **LLM calls are non-streaming** (`llm_router.chat()`); SSE endpoints replay completed
  results as frames via the unchanged `src/dashboard/chat_events.py`.
- **API contract preserved**: every Django route path is byte-identical to the old FastAPI
  one. `tests/contract/fastapi_routes.json` is the frozen baseline;
  `tests/contract/test_route_parity.py::test_no_path_drift` is the gate (run after any
  routing change). 126/127 routes ported; `/ws/agents` (WebSocket) needs Django Channels.

## ORM / models

- Models live in domain apps under `forgeos_web/`, mirror `infrastructure/database/*.sql`,
  and are **`managed=False`** with exact `db_table`. Composite-PK tables use
  `models.CompositePrimaryKey` (Django 5.2+). pgvector via `pgvector.django.VectorField`.
- Runtime works against the existing tables as-is. Django does NOT own migrations yet —
  the schema-ownership flip (`managed=True` + `migrate --fake-initial`, then the
  `rls_policies` RunSQL migration) is a deliberate, separate live-DB step
  (see `forgeos_web/MIGRATION_STATUS.md`).

## Conventions for adding/editing endpoints

- One `APIView` per path; hand-written `urlpatterns` mounted at `""` so the full path
  equals the FastAPI contract (no trailing slash; `{x}`→`<str:x>`, `{x:path}`→`<path:x>`).
- Response bodies are hand-built dicts/lists (that IS the contract) returned via DRF
  `Response`; don't wrap in serializers. Validate inputs with DRF serializers. Errors as
  `{"detail": ...}`.
- `permission_classes=[require_role(...)]` only where the route is gated. Platform objects
  from `di.get_context()`; acting user via `authn.context.acting_user`.
- SSE/chat are plain Django `async def` views returning `StreamingHttpResponse`. They MUST
  be decorated `@transaction.non_atomic_requests` (async views are incompatible with the
  DB's `ATOMIC_REQUESTS=True`, which the sync DRF views rely on for RLS).
- After routing changes, re-run `tests/contract/test_route_parity.py`.

## Testing

```bash
PYTHONPATH=. python manage.py check
PYTHONPATH=. python -m pytest tests/web tests/contract -v   # 11 tests + route parity
```

## Gotchas learned (don't re-discover these)

- **`ATOMIC_REQUESTS` + async views** → 500. The 3 async SSE chat views are
  `@transaction.non_atomic_requests`; keep `ATOMIC_REQUESTS=True` (RLS needs it on sync views).
- **Never name an app `secrets`** — it shadows Python's stdlib `secrets` (breaks
  `createsuperuser` password hashing). The user-credentials app is `usercreds`.
- **Composite-PK models can't be in Django admin** (Django forbids it); the auto-register
  in `rbac/admin.py` skips them. They're reachable via the API.
- **RLS needs a non-superuser DB role** — a superuser BYPASSes RLS and sees all tenants.
- **LLM gateway (atlas-router, OpenAI-compatible)**: provider `vllm` reads
  `VLLM_BASE_URL`/`VLLM_API_KEY`. Valid model ids are from the key's `/v1/models`
  (e.g. `qwen`, `gemini-3-flash`, `gemini-3.1-pro`) — NOT `qwen3.6-27b`. `qwen` is a
  reasoning model; `llm_router._call_openai` already handles it (65k token budget +
  `reasoning_content` fallback). Empty keys/sim → `[Simulated …]` responses.
- The dashboard proxies `/api/*` to the backend server-side (`FORGEOS_API_URL`) — no CORS in
  dev. Live agent status polls `/api/platform/agents` (it does NOT use `/ws/agents`).
