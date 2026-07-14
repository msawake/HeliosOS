# Contributing & Development

## Development Setup

```bash
git clone https://github.com/msawake/HeliosOS.git
cd HeliosOS/

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env with your API keys

# Boot the platform API (Django ASGI)
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

The **`forgeos` CLI** is installed by `pip install -e ".[dev]"`. Use `FORGEOS_API_URL=http://localhost:5000`.

### Optional dashboard UI

The Next.js operator dashboard is **not** in open-core. To run it locally:

```bash
git clone https://github.com/antonibergas-hue/forgeos-dashboard.git ../forgeos-dashboard
( cd ../forgeos-dashboard && npm install && echo 'FORGEOS_API_URL=http://localhost:5000' > .env.local )
```

Or `docker compose --profile ui up` with `../forgeos-dashboard` checked out.

## Running Tests

```bash
PYTHONPATH=. python3 -m pytest
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py -v
```

`PYTHONPATH=.` is required because both `src/` and `stacks/` are top-level packages.

## Code Style

- **Linting:** `ruff check src/ tests/`
- **Type checking:** `mypy src/`
- **Async:** use `async def` for I/O; tests use `asyncio_mode = "auto"`

## Project Structure Rules

1. **Framework code** goes in `src/platform/` or `src/core/`. MCP tool execution lives in `src/mcp/` (in-repo).
2. **HTTP API** goes in `forgeos_web/` (Django/DRF). `--dashboard` on bootstrap serves this layer.
3. **Stack adapters** go in `stacks/{name}/adapter.py` and implement `AgentStackAdapter`.
4. **Tests** mirror source: `test_platform_executor.py` for `src/platform/executor.py`.
5. **Infrastructure** goes in `infrastructure/` or `deploy/`.

Enterprise-only paths (`src/companies/`, billing, Pulumi, corporate orchestration) are documented in **heliosos-enterprise**, not this tree.

## Adding a New Stack Adapter

1. Create `stacks/yourstack/adapter.py` implementing `AgentStackAdapter`
2. Add `"yourstack"` to `STACK_NAMES` in `stacks/base.py`
3. Register in `src/bootstrap.py` `_register_adapters()`
4. Add tests in `tests/test_yourstack_adapter.py`

## Key Architecture Constraints

- **Graceful degradation:** external dependencies have in-memory fallbacks where possible
- **Agent isolation:** agents only use tools in their whitelist
- **Session locking:** concurrent invocations on the same session are serialized