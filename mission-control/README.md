# ForgeOS Mission Control

Operator console for the ForgeOS platform — Fleet / Governance / Cost / Topology / Manifest / Billing / MCP.

## Layout

```
mission-control/
├── backend/    FastAPI app, password gate, proxies /api/* to FORGEOS_API_URL
└── frontend/   Vite + React + TS + Tailwind, compiled to static/ and served by the backend
```

## Local development

Three terminals — Postgres, the platform, and the Vite dev server.

```bash
# from the repo root
make pg              # one-time: Postgres container on :5432
make migrate         # apply pending SQL migrations (idempotent)
make mc-platform     # boots src.bootstrap on :5099 (auto-frees the port)

# in another terminal
cd mission-control && make dev-local   # backend on :8888 + Vite on :5173
```

Visit `http://localhost:5173`. Vite HMR picks up frontend edits instantly;
backend changes require restarting `make mc-platform`. The root Makefile
pre-cleans the port before booting, so `address already in use` errors are
fixed automatically. To free a port without booting anything:

```bash
make free-port PORT=5099
```

## Features

- **Fleet** — every platform process. Scheduled agents display **SCHEDULED**
  (with next-run tooltip) between cron ticks and flip to **RUNNING** only
  while actively invoking. Per-row **STOP** (transition to stopped, keep in
  registry) and **DELETE** (stop + undeploy). `↑ UPLOAD AGENT` deploys a
  manifest from file or pasted YAML.
- **Right-side detail panel** — `▶ RUN NOW`, `STOP`, `DELETE`, plus a
  **Recent Runs** section showing the last 20 invocations (status, trigger,
  duration, tokens, tools, expandable prompt/output/error). Polled every 5 s.
- **RUN NOW** — fire-and-forget. Prompt optional. Modal closes immediately,
  results stream into Recent Runs and Governance → AGENT LOGS.
- **Governance** — unified HITL inbox (legacy approvals + A2H requests),
  kernel allow/deny feed, and an **AGENT LOGS** panel (run + tool events)
  with `all | runs | tools | hitl` filters, polled every 2 s.
- **MCP servers** — platform-scoped CRUD persisted in Postgres
  (`client_mcp_configs` under the `_platform` synthetic client). Restart
  required for changes to take effect.

See `docs/operations/mission-control.md` for the full feature, endpoint, and
schema reference.

## Production build (single container)

```bash
docker build -f infrastructure/docker/Dockerfile.mission-control -t mc:test .
docker run -p 8080:8080 \
  -e FORGEOS_API_URL=https://your-backend.run.app \
  -e FORGEOS_MC_PASSWORD=hunter2 \
  -e FORGEOS_API_TOKEN=xxx \
  mc:test
```

The image runs `uvicorn backend.main:app` on port 8080. The React bundle is baked into `backend/static/` at build time and served by FastAPI for any non-`/api`, non-`/login` route.

## Deployment

GitHub Actions (`.github/workflows/deploy.yml`) builds this image via Cloud Build and deploys it to Cloud Run as `forgeos-mission-control` on every push to `main`. No workflow changes were needed for the React migration — Cloud Build runs the multi-stage Dockerfile end-to-end.

## Environment

| Var | Required | Purpose |
|---|---|---|
| `FORGEOS_API_URL` | yes | Backend Cloud Run URL |
| `FORGEOS_API_TOKEN` | recommended | Bearer forwarded on every proxied request |
| `FORGEOS_MC_PASSWORD` | recommended | Operator password; empty disables the gate |
| `PORT` | auto | Set by Cloud Run; defaults to 8888 locally |
