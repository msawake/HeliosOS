# ForgeOS Mission Control — local dev shortcuts.
#
# `make install` creates a .venv and installs backend + frontend deps.
# `make serve` boots the backend against the SAME upstream ForgeOS API
# (and therefore the same database) the deployed Cloud Run service uses —
# FORGEOS_API_URL is read from the deployed service, FORGEOS_API_TOKEN from
# Secret Manager. Nothing hardcoded.

PROJECT          ?= YOUR_PROJECT_ID
REGION           ?= europe-west1
MC_SERVICE       ?= forgeos-mission-control
API_TOKEN_SECRET ?= forgeos-api-token

BACKEND_PORT  ?= 8888
FRONTEND_PORT ?= 5173

# Local-platform target used by `make serve-local` / `make dev-local`.
# Override with `make dev-local LOCAL_API=http://localhost:5000`.
LOCAL_API ?= http://localhost:5099

# Default to the newest Python available on PATH (>=3.10 required for PEP 604 unions).
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || echo python3)
VENV   := .venv
VPY    := $(VENV)/bin/python
VPIP   := $(VENV)/bin/pip

.PHONY: help install venv backend-install frontend-install serve serve-prod \
        serve-local dev dev-local frontend build clean check-gcloud

help:
	@echo "Mission Control — make targets"
	@echo ""
	@echo "  make install       Create .venv and install backend + frontend deps."
	@echo "  make serve         Run backend (no password gate) against the deployed ForgeOS API."
	@echo "  make serve-prod    Same as serve, but pulls the operator password from Secret Manager."
	@echo "  make serve-local   Run backend against a LOCAL ForgeOS API ($(LOCAL_API)). No gcloud."
	@echo "  make dev           Run backend AND vite dev server in parallel."
	@echo "  make dev-local     Same as dev, but against a local ForgeOS API (no gcloud)."
	@echo "  make frontend      Run only the vite dev server on :$(FRONTEND_PORT)."
	@echo "  make build         Build the React bundle into frontend/dist."
	@echo "  make clean         Remove .venv, node_modules, dist, __pycache__."
	@echo ""
	@echo "Vars: PROJECT=$(PROJECT) REGION=$(REGION) MC_SERVICE=$(MC_SERVICE) PYTHON=$(PYTHON)"

# ---------- install ----------

install: backend-install frontend-install
	@echo "✓ install complete. Activate the venv with: source $(VENV)/bin/activate"

venv: $(VPY)

$(VPY):
	@echo "→ creating venv at $(VENV) (using $(PYTHON))..."
	@$(PYTHON) -m venv $(VENV)
	@$(VPIP) install -q --upgrade pip

backend-install: venv
	@echo "→ installing backend deps..."
	@$(VPIP) install -q -r backend/requirements.txt

frontend-install:
	@[ -d frontend/node_modules ] || (echo "→ installing frontend deps..." && cd frontend && npm install)

# ---------- serve ----------

check-gcloud:
	@command -v gcloud >/dev/null || { echo "gcloud CLI required"; exit 1; }
	@gcloud auth print-access-token >/dev/null 2>&1 || { \
	  echo "Not authenticated. Run: gcloud auth login"; exit 1; }

serve: check-gcloud backend-install backend/static/index.html
	@echo "→ resolving FORGEOS_API_URL from deployed $(MC_SERVICE)..."
	@FORGEOS_API_URL="$$(gcloud run services describe $(MC_SERVICE) \
	    --project=$(PROJECT) --region=$(REGION) \
	    --format='value(spec.template.spec.containers[0].env)' \
	    | tr ';' '\n' | sed -n "s/.*'FORGEOS_API_URL'.*'value': '\\([^']*\\)'.*/\\1/p")"; \
	echo "→ FORGEOS_API_URL=$$FORGEOS_API_URL"; \
	echo "→ fetching $(API_TOKEN_SECRET) from Secret Manager..."; \
	FORGEOS_API_TOKEN="$$(gcloud secrets versions access latest \
	    --project=$(PROJECT) --secret=$(API_TOKEN_SECRET))"; \
	FORGEOS_API_URL="$$FORGEOS_API_URL" \
	  FORGEOS_API_TOKEN="$$FORGEOS_API_TOKEN" \
	  FORGEOS_MC_PASSWORD="" \
	  PORT=$(BACKEND_PORT) \
	  $(VPY) -m uvicorn backend.main:app --reload --port $(BACKEND_PORT)

serve-prod: check-gcloud backend-install backend/static/index.html
	@FORGEOS_API_URL="$$(gcloud run services describe $(MC_SERVICE) \
	    --project=$(PROJECT) --region=$(REGION) \
	    --format='value(spec.template.spec.containers[0].env)' \
	    | tr ';' '\n' | sed -n "s/.*'FORGEOS_API_URL'.*'value': '\\([^']*\\)'.*/\\1/p")"; \
	FORGEOS_API_TOKEN="$$(gcloud secrets versions access latest \
	    --project=$(PROJECT) --secret=$(API_TOKEN_SECRET))"; \
	FORGEOS_MC_PASSWORD="$$(gcloud secrets versions access latest \
	    --project=$(PROJECT) --secret=forgeos-mc-password)"; \
	cd backend && \
	  FORGEOS_API_URL="$$FORGEOS_API_URL" \
	  FORGEOS_API_TOKEN="$$FORGEOS_API_TOKEN" \
	  FORGEOS_MC_PASSWORD="$$FORGEOS_MC_PASSWORD" \
	  PORT=$(BACKEND_PORT) \
	  ../$(VPY) -m uvicorn main:app --reload --port $(BACKEND_PORT)

serve-local: backend-install backend/static/index.html
	@echo "→ serving against LOCAL ForgeOS API at $(LOCAL_API) (override with LOCAL_API=...)"
	FORGEOS_API_URL="$(LOCAL_API)" \
	  FORGEOS_API_TOKEN="" \
	  FORGEOS_MC_PASSWORD="" \
	  PORT=$(BACKEND_PORT) \
	  $(VPY) -m uvicorn backend.main:app --reload --port $(BACKEND_PORT)

dev: frontend-install
	@$(MAKE) -j2 serve frontend

dev-local: frontend-install
	@$(MAKE) -j2 serve-local frontend

frontend: frontend-install
	cd frontend && npm run dev -- --port $(FRONTEND_PORT)

build: frontend-install
	cd frontend && npm run build
	@rm -rf backend/static
	@cp -R frontend/dist backend/static
	@echo "✓ frontend built → backend/static/"

# Make `serve` self-sufficient: build the bundle once if missing.
backend/static/index.html:
	@$(MAKE) build

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist
	find backend -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
