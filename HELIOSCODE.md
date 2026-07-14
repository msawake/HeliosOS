# HELIOSCODE.md — ForgeOS Open-Core Developer Guide

Welcome to the **ForgeOS Open-Core Developer Guide**. This document serves as the master guide for public developers working with the open-source core of ForgeOS. 

It outlines the repository layout, architectural principles, running the local development server, and how the open-source core is designed to be extended with proprietary enterprise modules.

---

## 1. Repository Layout (Open-Core)

Following our strategic decoupling, the public repository has been sanitized into a lightweight, highly focused developer SDK:

```
/src/                       PLATFORM LIBRARY (The core agent-native engine)
    bootstrap.py             Boots the platform (7 phases) then serves Django ASGI
    platform/                Executor, kernel, registry, scheduler, llm_router, ...
    runtime/                 Durable execution engine (queue, ledger, StepEngine, resume)
    api/auth.py              Framework-agnostic crypto + AuthManager (tokens/PBKDF2/API keys)
    core/                    DatabaseClient (raw psycopg), migrations runner
/stacks/                    Agent stack adapters (forgeos, crewai, adk, openclaw)
/a2h/                       Agent-to-human protocol
/tests/                     Core platform unit and integration tests
```

*Note: Proprietary enterprise modules (Next.js dashboard UI, `src/billing/`, `src/companies/`, corporate orchestration, and `pulumi/` infrastructure) are **not** in open-core. The `--dashboard` bootstrap flag starts the **Django API** (`forgeos_web/`), not a browser UI. Optional UI: [forgeos-dashboard](https://github.com/antonibergas-hue/forgeos-dashboard).*

---

## 2. Running the Local Development Server

To boot the open-source platform and start running agents, ensure you have PostgreSQL and Redis running locally, then execute the bootstrap script:

```bash
# 1. Configure environment variables
export DATABASE_URL=postgresql://USER:PW@HOST:5432/DB
export REDIS_URL=redis://HOST:6379/0
export VLLM_BASE_URL=https://atlas-router.example.com/v1
export VLLM_API_KEY=<your-gateway-key>

# 2. Run the bootstrap server
PYTHONPATH=.:a2h python -m src.bootstrap --no-auth --port 5000
```

---

## 3. Core Architectural Principles

### A. The "Agent-Native" Paradigm
ForgeOS is built from the ground up for AI agents, not human operators. Every process, capability, and tool call is governed by the **Kernel Facade** (`src/platform/kernel/_facade.py`) and executed via secure system calls (`src/platform/kernel/_syscall.py`).

### B. Pluggable Enterprise Extension Seams
The open-source core is designed to be fully functional out-of-the-box, but includes clean, abstract seams that allow it to be extended with proprietary enterprise packages (like `heliosos-enterprise`):

* **License Gating:** The kernel's admission and tool execution paths check for a `license_manager`. If an enterprise license package is installed, it enforces strict subscription gates; if not, it falls back to a permissive local development mode.
* **Cryptographic Auditing:** The core `SyscallGate` defines an abstract audit hook. If the enterprise package is present, it injects the **SHA-256 Hash-Chained Audit Ledger**; otherwise, it falls back to standard local JSON logging.
* **Durable Continuations:** The runtime engine supports pluggable continuation stores. It uses a simple in-memory dictionary for local development, which can be swapped for a Redis-backed durable store in production.

---

## 4. SaaS Monetization & Production Licensing

To support commercialization, the open-source core enforces a strict **Production Licensing Gate**:
* **Local Development:** When `FORGEOS_KERNEL_MODE` is unset or set to `"development"`, developers can build, run, and test agents locally for free.
* **Production Deployment:** When deploying to a production environment, administrators must set `FORGEOS_KERNEL_MODE="production"`. In this mode, the kernel strictly requires a valid, active **Enterprise License Key** to admit agent processes or execute tool calls. If no key is present, execution is blocked with a clear warning directing the user to purchase a key.

To purchase an Enterprise License Key and unlock advanced capabilities (Stateful Autonomous Loops, Durable Continuation Stores, and Cryptographic Auditing), visit [Making Science](https://makingscience.com).

---

## 5. Contributing & Git Workflow Standards

To maintain clean, professional contributions, all commits in this repository must follow the established **Git Provenance Standards**:

* **Commit Message Format:** Follow [Conventional Commits](https://www.conventionalcommits.org/) (e.g., `feat(kernel): ...`, `fix(router): ...`).
* **Co-Author Attribution:** When commits are produced with AI coding-agent assistance, credit the agent git identity (not a human GitHub user — see [CONTRIBUTORS.md](CONTRIBUTORS.md)):
  ```
  Co-authored-by: helioscode <helioscode@users.noreply.github.com>
  Co-authored-by: awake <awake@makingscience.com>
  ```
  `helioscode` is the project's AI coding agent (comparable to Claude Code), not [github.com/helioscode](https://github.com/helioscode).
