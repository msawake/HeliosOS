# ForgeOS Mission Control

Operator console for the ForgeOS platform — Fleet / Governance / Cost / Topology / Manifest / Billing.

## Layout

```
mission-control/
├── backend/    FastAPI app, password gate, proxies /api/* to FORGEOS_API_URL
└── frontend/   Vite + React + TS + Tailwind, compiled to static/ and served by the backend
```

## Local development

Two terminals.

**Backend** (port 8888):

```bash
cd mission-control/backend
pip install -r requirements.txt
FORGEOS_API_URL=http://localhost:5000 \
  FORGEOS_API_TOKEN=dev \
  python -m uvicorn main:app --reload --port 8888
```

**Frontend** (port 5173, proxies /api and /login to 8888):

```bash
cd mission-control/frontend
npm install
npm run dev
```

Visit `http://localhost:5173`.

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
