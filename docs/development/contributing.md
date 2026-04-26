# Contributing & Development

## Development Setup

```bash
# Clone and install
cd ~/Documents/one
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Dashboard
cd dashboard && npm install && cd ..

# Configure
cp .env.example .env
# Edit .env with your API keys
```

## Running Tests

```bash
# All tests (~900 tests, ~20s)
PYTHONPATH=. python3 -m pytest

# Single file
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py

# By name pattern
PYTHONPATH=. python3 -m pytest -k "test_deploy"

# With verbose output
PYTHONPATH=. python3 -m pytest -v

# Stop on first failure
PYTHONPATH=. python3 -m pytest -x
```

`PYTHONPATH=.` is required because both `src/` and `stacks/` are top-level packages.

## Code Style

- **Linting:** `ruff check src/ tests/`
- **Type checking:** `mypy src/`
- **Formatting:** Follow existing patterns. No trailing whitespace.
- **Imports:** Standard library, third-party, local. Each group alphabetical.
- **Docstrings:** Module-level docstrings required. Function docstrings for public methods.
- **Async:** Use `async def` for all I/O-bound operations. Tests use `asyncio_mode = "auto"`.

## Project Structure Rules

1. **Framework code** goes in `src/platform/`, `src/core/`, or `src/mcp/`.
2. **Stack adapters** go in `stacks/{name}/adapter.py` and implement `AgentStackAdapter`.
3. **Company packages** go in `src/companies/{id}/` with `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`.
4. **Tests** mirror source structure: `test_platform_executor.py` for `src/platform/executor.py`.
5. **Agent configurations** go in `agents/` (gitignored, generated at runtime).
6. **Infrastructure** goes in `infrastructure/` or `deploy/`.

## Adding a New Stack Adapter

1. Create `stacks/yourstack/__init__.py` and `stacks/yourstack/adapter.py`
2. Implement all `AgentStackAdapter` methods
3. Set `stack_name = "yourstack"`
4. Add `"yourstack"` to `STACK_NAMES` in `stacks/base.py`
5. Register in `src/bootstrap.py` `_register_adapters()`
6. Add tests in `tests/test_yourstack_adapter.py`

## Adding a New Company Package

1. Create `src/companies/mycompany/` with required files
2. Add entry in `src/config/agent_configs.py` for config loading
3. Create `src/companies/mycompany/config.yaml` with budgets, models, and MCP servers
4. Test with `python -m src.bootstrap --company mycompany`

## Key Architecture Constraints

- **No cross-tenant data:** All DB queries go through `tenant()` context manager with RLS.
- **Graceful degradation:** Every external dependency has an in-memory fallback.
- **Agent isolation:** Agents can only use tools in their whitelist.
- **Crash safety:** Autonomous loops use QUARANTINED status after repeated crashes.
- **Session locking:** Concurrent invocations on the same session are serialized via `asyncio.Lock`.
