# ForgeOS — Running the Platform

## Components Overview

| Component | Runtime | Port | Required |
|---|---|---|---|
| **PostgreSQL** (with pgvector) | Docker | 5433 (host) / 5432 (internal) | Yes |
| **Redis** | Docker | 6379 | Yes (rate limiting, pub/sub, sessions) |
| **FastAPI Backend** | Python 3.11 | 5000 | Yes |
| **Next.js Dashboard** | Node 22 | 3000 | Dev UI only |
| **A2H Gateway** | Python (subprocess) | — | Only for human-in-the-loop flows |
| **MCP Server** | Python (subprocess) | — | Only for MCP tool use |
| **Sandbox runner** | Docker-in-Docker | — | Only for sandbox stack agents |

---

## Tool Requirements

### Python

- **Python 3.11+** (Makefile defaults to `/opt/homebrew/opt/python@3.11/bin/python3.11`)
- Recommended: install via [mise](https://mise.jdx.dev/) or pyenv

```toml
# mise.toml example
[tools]
python = "3.11"
node = "22"
```

### Node.js

- **Node 22** (used in dashboard `Dockerfile` base image: `node:22-alpine`)
- Recommended: install via mise or nvm

### Docker

Required to run PostgreSQL and Redis locally (and for sandbox agents).

```bash
docker --version        # Docker 24+
docker compose version  # Compose v2
```

---

## Environment Variables

Copy `.env.example` to `.env` in the repo root and fill in values:

```bash
cp .env.example .env
```

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (for real LLM calls) | Leave empty for simulation mode |
| `OPENAI_API_KEY` | No | Only for GPT/o3 model routing |
| `DATABASE_URL` | No (dev) | Defaults to in-memory if unset; format: `postgresql://user:pass@host:5432/db` |
| `REDIS_URL` | No (dev) | Defaults to in-memory if unset; format: `redis://localhost:6379` |
| `FORGEOS_API_URL` | No | Used by Next.js dev server rewrites; default `http://localhost:5000` |
| `FORGEOS_SYSCALL_PIPELINE` | No | Set to `1` to activate the new kernel syscall pipeline instead of the legacy hooks chain |

---

## Quick Start — Dev (no Docker, in-memory DB)

The platform degrades gracefully: no DB → in-memory, no Redis → in-memory, no API key → simulation.

```bash
# 1. Install Python deps + dashboard Node modules
pip install -e ".[dev]"
# or: make install-dev && make install-all (also runs npm install in dashboard/)

# 2. Set at minimum your Anthropic key
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env

# 3. Boot the platform (no auth, in-memory, port 5000)
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
# or: make run

# 4. In a separate terminal — start the Next.js dashboard
cd dashboard && npm run dev
# or: make dashboard
# → http://localhost:3000
```

---

## Docker Compose — Full Local Stack

This is the recommended way to run PostgreSQL + Redis alongside the API.

### Dockerfiles

| Image | File | Target stage | What it is |
|---|---|---|---|
| **API** | `infrastructure/docker/Dockerfile` | `api` | Python 3.12-slim, production deps, runs `src.bootstrap` |
| **Sandbox** | `infrastructure/docker/Dockerfile.sandbox` | _(single stage)_ | Minimal Python 3.12-slim image for isolated agent sandboxes |
| **Dashboard** | `dashboard/Dockerfile` | `runner` | Node 22 Alpine, Next.js standalone build |

### Setup & Run

```bash
# 1. Generate .env with a random DB_PASSWORD
bash infrastructure/docker/docker-setup.sh

# 2. Add your Anthropic key to infrastructure/docker/.env
echo "ANTHROPIC_API_KEY=sk-ant-..." >> infrastructure/docker/.env

# 3. Build and start all services (postgres, redis, forgeos API)
cd infrastructure/docker
docker compose up --build

# API is available at http://localhost:5000
# Postgres exposed on host port 5433 (avoids conflict with local Postgres on 5432)
# Redis on 6379
```

The `forgeos` service mounts `/var/run/docker.sock` to spawn sandbox containers and a `knowledge_data` volume for knowledge base files.

### Sandbox networking

Sandbox agents run in sibling Docker containers on the `forgeos-internal` bridge network. The API container is in both `default` and `forgeos-internal` networks. The sandbox image is built separately:

```bash
docker build -f infrastructure/docker/Dockerfile.sandbox -t forgeos-sandbox:latest .
```

---

## Python Dependency Extras

Install only what you need:

```bash
pip install -e ".[dev]"               # pytest, ruff, mypy — local development
# or: make install-dev

pip install -e ".[dev,openai]"        # + OpenAI GPT/o3 routing
pip install -e ".[dev,mcp]"           # + MCP protocol client
pip install -e ".[dev,crewai]"        # + CrewAI stack adapter
pip install -e ".[dev,adk]"           # + Google ADK stack adapter
pip install -e ".[dev,scheduler]"     # + APScheduler (cron jobs)
pip install -e ".[dev,database]"      # + psycopg3 + redis (real DB/cache)
pip install -e ".[dev,observability]" # + Prometheus + OpenTelemetry
pip install -e ".[production]"        # all production extras (no dev tools)
# or: make install-prod
pip install -e ".[dev,all-providers]" # dev + openai + mcp
```

---

## Running Each Component Individually

### Backend API only

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
# or: make run
# or with custom port: make run PORT=5001
```

Flags:
- `--no-auth` — skip JWT validation (dev only)
- `--dashboard` — mount the FastAPI REST endpoints
- `--loop` — start the scheduler + event bus background loop
- `--company leadforge` — load a company agent pack on boot
- `--port N` — listen on port N (default 5000)

### With syscall kernel pipeline

```bash
FORGEOS_SYSCALL_PIPELINE=1 PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

### Production-shaped boot (company pack + scheduler)

```bash
PYTHONPATH=. python3 -m src.bootstrap --company leadforge --dashboard --loop --port 5000
```

### Next.js Dashboard

```bash
cd dashboard && npm run dev
# or: make dashboard
# → http://localhost:3000
```

Communicates with the backend via `FORGEOS_API_URL` (default `http://localhost:5000`).

### Postgres + Redis only (via Docker)

```bash
cd infrastructure/docker
docker compose up postgres redis
```

Then set in `.env`:

```bash
DATABASE_URL=postgresql://leadforge_admin:<DB_PASSWORD>@localhost:5433/leadforge
REDIS_URL=redis://localhost:6379
```

### Database migrations

```bash
PYTHONPATH=. python3 -m src.core.migrations
# or: make migrate
```

There are 7 SQL migrations in `infrastructure/database/` (001–007). The migration runner applies them in order on boot if `DATABASE_URL` is set.

---

## CLI

- environment
	- pod de kubernetes
		- medio giga de ram
	- pueden correr varios agentes
		- pueden tener diferentes permisos y comunicarse
		- hacer que el agente se encarge de los ficheros de la agencia y de otra agencia
		- hablan para transmitir tecnicas sin compartir cosas confidenciales
			- 3 agentes con cosas confidenciales
			- 
- agente
	- puede estar en 2 environments?
		- con en ambos manifests.
	- 
	- manifest.mf que guia el behaviour del agente
		- (cosas hechas y que no cuadran, pero deberia te3ner)
		- modelo al que va
		- system prompt
		- otros datos de sistema operativo
		- memoria
		- always-on ( siempre corre)
			- se enciente reactivamente
			- cada media hora se enciente y todos los archivos
			- un archivo , haces un analisis
			- always on
				- tuneando archivo para que responda a preguntas concretas y todo el rato
				- 
	- memoria
	- sytem policy
		- a que acede en el ordenador
		- permisos de syscall - sdk runtime
			- budget tengo o permiso a un humano
- agent to human protocol
	- acceder a mi tal o enviar correo
		- preguntar al humano
		- human in the loop
		- approvals, no aplica agentic human
	- revisar implementacion A2H (TAREA)
		- agent to human
		- define agente y si se puede desplegar en 1 agente
		- desplegar que comentaba en jira
		- a2h publico
	- (TAREA) 1er caso de uso
		- MatIAs
			- compartir clave jira y crear manifest que tenga
				- jira token
				- encargarse de hacer reactivo
					- o cron
				- colgar o publicar ticket de ally partner que asigne a cada una de las personas de ally partner
					- y revisar 
				- rotar tickets:
					- hector
					- laura
					- dani
					- toni
				- Extra: puntos de estilo
					- agente envia correo
					- GWS mediante MCP
					- asignar correo a hector, que el agente le mande correo a hector
					- que es automatizado y que se le ha asignado
						- se explica
						- que se explique un resumen del 
					- Creo que he resuelto la incidencia
						- A2H pedir permiso para contestarle
					- no me funciona el modelo x y saber que el modelo se enciente a las 5pm
				- Tener memoria y referencias
				- si lo escribes a opus va a hardcodear cosas
			- desde UI hacer sencillo para hacer diferentes acciones
	- progresivo
		- he visto este ticket
			- reactivo y que puede ver acciones como notificaciones
			- cambiar el autor del ticket
			- ir a por el token de matias
- http://35.240.72.99/environments

The `forgeos` CLI is installed as a console script by `pyproject.toml`:

```bash
pip install -e .

forgeos deploy agent.yaml    # validate + POST to /api/platform/agents
forgeos list                 # list deployed agents
forgeos invoke <id> "prompt" # invoke an agent
forgeos undeploy <id>        # remove an agent
forgeos health               # platform health check
```

The Makefile also exposes the CLI runner for local use (without installing the package globally):

```bash
make forgeos   # PYTHONPATH=. python3 backend/forgeos
```

---

## MCP Server

The ForgeOS MCP server lives in `tools/forgeos-mcp-server.py`. It is loaded at platform boot via `.mcp.json` configuration. No separate startup needed in dev — the `MCPServerManager` in `src/mcp/server_manager.py` handles lifecycle.

---

## Testing

```bash
# All tests (~1132 tests)
PYTHONPATH=. python3 -m pytest
# or: make test

# Single file
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py
# or: make test-file FILE=tests/test_platform_executor.py

# Pattern
PYTHONPATH=. python3 -m pytest -k "test_kernel"
# or: make test-match K=test_kernel

# A2H protocol conformance
PYTHONPATH=. python3 -m pytest a2h/tests/

# Company vertical tests
PYTHONPATH=. python3 -m pytest examples/companies/tests/

# Load tests (k6 required)
k6 run tests/load/smoke.js
```

### Code quality

```bash
ruff check src/ tests/    # lint        — or: make lint
mypy src/                 # type check  — or: make typecheck
make check                # both (lint + typecheck)
```

### Cleanup

```bash
make clean   # removes __pycache__, .pytest_cache, .mypy_cache, .ruff_cache, build/, dist/
```

---

## Kubernetes (staging/prod)

Manifests in `deploy/k8s/`. Uses Kustomize overlays:

```bash
# Dev overlay
kubectl apply -k deploy/k8s/overlays/dev

# Production
kubectl apply -k deploy/k8s/overlays/prod
```

Terraform for GCP infrastructure (Cloud SQL, Redis, Cloud Run, VPC, Secret Manager):

```bash
cd infrastructure/terraform/gcp
terraform init && terraform plan
```
