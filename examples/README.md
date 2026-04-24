# ForgeOS Examples

Hello-world agents for all 4 stack adapters. Each example deploys, invokes, and verifies output.

## Prerequisites

```bash
# Backend running
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# At least one LLM API key in .env
OPENAI_API_KEY=sk-...
# or ANTHROPIC_API_KEY=sk-ant-...
```

## Run All 4 Stacks

```bash
PYTHONPATH=. python3 examples/run_all_hello_world.py
```

Output:
```
ForgeOS Native (forgeos)  ✓ OK
CrewAI (crewai)           ✓ OK
Google ADK (adk)          ✓ OK
OpenClaw (openclaw)       ~ FALLBACK (requires gateway setup)
```

## Individual Examples

Each framework has both a YAML manifest and a Python script:

### ForgeOS Native
```bash
PYTHONPATH=. python3 examples/forgeos/hello_world.py
```
Uses the platform's built-in agentic loop. No SDK dependencies.

### CrewAI
```bash
PYTHONPATH=. python3 examples/crewai/hello_world.py
```
If `crewai` is installed, runs through `Crew.kickoff()`. Otherwise, falls back to ForgeOS native loop.

### Google ADK
```bash
PYTHONPATH=. python3 examples/adk/hello_world.py
```
If `google-adk` is installed, runs through `Runner.run_async()`. Otherwise, falls back to ForgeOS native loop.

### OpenClaw
```bash
PYTHONPATH=. python3 examples/openclaw/hello_world.py
```
If the OpenClaw gateway (`openclaw2/`) is configured, uses HTTP API. Otherwise, falls back to ForgeOS native loop.

## Deploy via YAML

Each example includes an `agent.yaml` manifest that can be deployed via CLI:

```bash
forgeos deploy examples/forgeos/hello-world.yaml
forgeos deploy examples/crewai/hello-world.yaml
forgeos deploy examples/adk/hello-world.yaml
forgeos deploy examples/openclaw/hello-world.yaml
```

## Structure

```
examples/
  run_all_hello_world.py     # Master runner — deploys and tests all 4
  forgeos/
    hello-world.yaml         # YAML manifest
    hello_world.py           # Python SDK example
  crewai/
    hello-world.yaml
    hello_world.py
  adk/
    hello-world.yaml
    hello_world.py
  openclaw/
    hello-world.yaml
    hello_world.py
```

## What Each Example Demonstrates

1. **Agent declaration** — using the SDK `Agent` class (declarative style)
2. **Deployment** — via `ForgeOSClient.deploy(manifest)`
3. **Invocation** — via `ForgeOSClient.invoke(agent_id, prompt)`
4. **Result handling** — status, output, tokens, warnings
5. **Graceful fallback** — if SDK not installed, agents still work via ForgeOS native loop
