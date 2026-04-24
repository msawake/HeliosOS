# ForgeOS Terminal Usage Guide

Complete guide for using ForgeOS from the terminal - API, SDK, and CLI.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Starting the Platform](#starting-the-platform)
3. [Using the Python SDK](#using-the-python-sdk)
4. [Using the CLI](#using-the-cli)
5. [Using the REST API](#using-the-rest-api)
6. [Common Workflows](#common-workflows)
7. [Troubleshooting](#troubleshooting)

---

## Quick Start

### 1. Install Dependencies

```bash
cd ~/Documents/one
pip install -e ".[dev]"
```

### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env and add your API keys
# Required: At least one LLM provider
export ANTHROPIC_API_KEY="sk-ant-..."
# OR
export OPENAI_API_KEY="sk-..."
```

### 3. Start the Platform

```bash
# Start ForgeOS with API server
PYTHONPATH=. python3 -m src.bootstrap \
  --company leadforge \
  --dashboard \
  --loop \
  --port 5000 \
  --no-auth
```

### 4. Verify It's Running

```bash
# Check health
curl http://localhost:5000/api/health

# Or use the CLI
forgeos health
```

---

## Starting the Platform

### Basic Startup

```bash
# Minimal (API only, no scheduler)
PYTHONPATH=. python3 -m src.bootstrap --dashboard --port 5000

# Full platform (API + scheduler + workflows)
PYTHONPATH=. python3 -m src.bootstrap \
  --company leadforge \
  --dashboard \
  --loop \
  --port 5000
```

### Startup Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--company <id>` | Company ID | leadforge |
| `--mode <mode>` | Execution mode (shadow/supervised/autonomous) | supervised |
| `--dashboard` | Start FastAPI server | false |
| `--loop` | Run main operational loop (scheduler, workflows) | false |
| `--port <port>` | API listen port | 5000 |
| `--no-auth` | Disable API authentication (dev only) | false |

### Environment Variables

```bash
# API Configuration
export PORT=5000
export FORGEOS_ALLOW_DEV_LOGIN=1
export FORGEOS_DEV_PASSWORD=forgeos

# LLM Providers (at least one required)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="..."

# Database (optional, uses in-memory if not set)
export DATABASE_URL="postgresql://user:pass@localhost/forgeos"

# Redis (optional)
export REDIS_URL="redis://localhost:6379"
```

### Check Platform Status

```bash
# Health check
curl http://localhost:5000/api/health | jq .

# Readiness probe
curl http://localhost:5000/api/readiness | jq .

# Metrics
curl http://localhost:5000/metrics
```

---

## Using the Python SDK

### Installation

```bash
pip install -e .
```

### Basic Usage

```python
from forgeos_sdk import Agent, ForgeOSClient

# Create an agent manifest
manifest = (Agent.builder("my-agent")
    .forgeos()                          # Stack: forgeos, crewai, adk, openclaw
    .reflex()                           # Execution type
    .model("gpt-4o", provider="openai") # LLM config
    .tools("mcp__filesystem__*")        # Tools
    .prompt("You are a helpful agent")  # System prompt
    .build())

# Deploy the agent
with ForgeOSClient(base_url="http://localhost:5000") as client:
    agent_id = client.deploy(manifest)
    print(f"Deployed: {agent_id}")
    
    # Invoke the agent
    result = client.invoke(agent_id, "Hello!")
    print(result["result"])
```

### Declarative Style (Class-Based)

```python
from forgeos_sdk import Agent, ForgeOSClient

class EmailChecker(Agent):
    name = "email-checker"
    description = "Checks email 3x daily"
    department = "operations"
    
    stack = "forgeos"
    execution_type = "scheduled"
    schedule = "0 7,12,17 * * *"
    
    model = "gpt-4o"
    provider = "openai"
    
    tools = ["mcp__filesystem__*", "company__publish_event"]
    system_prompt = "You check email and summarize..."

# Deploy
with ForgeOSClient() as client:
    agent_id = client.deploy(EmailChecker.manifest())
```

### Client Methods

```python
from forgeos_sdk import ForgeOSClient

client = ForgeOSClient(base_url="http://localhost:5000")

# Agent Lifecycle
agent_id = client.deploy(manifest)           # Deploy from manifest
agents = client.list()                       # List all agents
agents = client.list(stack="forgeos")        # Filter by stack
agent = client.get(agent_id)                 # Get agent details
client.update(agent_id, new_manifest)        # Update agent
client.stop(agent_id)                        # Stop agent
client.undeploy(agent_id)                    # Remove agent

# Invocation
result = client.invoke(agent_id, "prompt")   # Non-streaming invoke

# Streaming chat
for event in client.chat_stream(agent_id, "message", session_id="s1"):
    if event["type"] == "text_delta":
        print(event["content"], end="", flush=True)

# Events
client.fire_event("cost.exceeded", {"amount": 100})

# Approvals
approvals = client.list_approvals()
client.approve("req-123", approved_by="user@example.com")
client.reject("req-123", rejected_by="user@example.com")

# Platform
overview = client.overview()
health = client.health()

client.close()
```

### Execution Types

```python
# Reflex (on-demand)
Agent.builder("chat-bot").reflex()

# Scheduled (cron)
Agent.builder("daily-report").scheduled("0 9 * * *")

# Always-on (continuous loop)
Agent.builder("monitor").always_on()

# Event-driven
Agent.builder("alert-responder").event_driven("cost.exceeded", "error.critical")

# Autonomous (goal-directed)
Agent.builder("researcher").autonomous(goal="Find top 5 competitors")
```

### Example Scripts

```bash
# Run hello world example
PYTHONPATH=. python examples/forgeos/hello_world.py

# Deploy all agents from YAML files
PYTHONPATH=. python examples/deploy.py

# Test all agents
PYTHONPATH=. python examples/test.py
```

---

## Using the CLI

### Installation

The CLI is automatically installed when you run `pip install -e .`

```bash
forgeos --help
```

### Commands

#### Deploy an Agent

```bash
# From YAML file
forgeos deploy ./agent.yaml

# From JSON file
forgeos deploy ./agent.json

# With custom API URL
forgeos deploy ./agent.yaml --url http://localhost:5000

# With API key
forgeos deploy ./agent.yaml --api-key fos_...
```

#### List Agents

```bash
# List all agents
forgeos list

# Output example:
# AGENT_ID        NAME              STACK      TYPE         STATUS
# ────────────────────────────────────────────────────────────────
# agent-123       email-checker     forgeos    scheduled    running
# agent-456       chat-bot          forgeos    reflex       idle
```

#### Invoke an Agent

```bash
# Invoke with a prompt
forgeos invoke agent-123 "Check my inbox"

# Output: JSON result
```

#### Get Agent Details

```bash
forgeos get agent-123
```

#### Undeploy an Agent

```bash
forgeos undeploy agent-123
```

#### Validate Manifest

```bash
# Validate without deploying
forgeos validate ./agent.yaml

# Output:
# ✓ Manifest valid: my-agent
#   Stack:          forgeos
#   Execution type: scheduled
#   Model:          gpt-4o (openai)
#   Schedule:       0 9 * * *
```

#### Check Platform Health

```bash
forgeos health
```

### Environment Variables for CLI

```bash
# Set default API URL
export FORGEOS_API_URL=http://localhost:5000

# Set API key
export FORGEOS_API_KEY=fos_...

# Then use CLI without flags
forgeos list
```

---

## Using the REST API

### Authentication

#### No Auth (Development)

```bash
# Start with --no-auth flag
PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000

# No auth headers needed
curl http://localhost:5000/api/platform/agents
```

#### API Key Auth

```bash
# Use X-API-Key header
curl http://localhost:5000/api/platform/agents \
  -H "X-API-Key: fos_..."
```

#### Bearer Token (Dev Mode)

```bash
# Get token
TOKEN=$(curl -X POST http://localhost:5000/api/auth/token \
  -H "Content-Type: application/json" \
  -d '{"password": "forgeos"}' | jq -r '.token')

# Use token
curl http://localhost:5000/api/platform/agents \
  -H "Authorization: Bearer $TOKEN"
```

### Key Endpoints

#### Health & Status

```bash
# System health
curl http://localhost:5000/api/health | jq .

# Readiness probe
curl http://localhost:5000/api/readiness | jq .

# Prometheus metrics
curl http://localhost:5000/metrics
```

#### Agent Management

```bash
# List all agents
curl http://localhost:5000/api/platform/agents | jq .

# Filter by stack
curl "http://localhost:5000/api/platform/agents?stack=forgeos" | jq .

# Filter by execution type
curl "http://localhost:5000/api/platform/agents?execution_type=reflex" | jq .

# Get specific agent
curl http://localhost:5000/api/platform/agents/agent-123 | jq .

# Deploy agent
curl -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "chat_model": "gpt-4o",
    "provider": "openai",
    "tools": ["mcp__filesystem__*"],
    "system_prompt": "You are a helpful agent"
  }' | jq .

# Update agent
curl -X PUT http://localhost:5000/api/platform/agents/agent-123 \
  -H "Content-Type: application/json" \
  -d '{
    "tools": ["mcp__filesystem__*", "company__publish_event"],
    "system_prompt": "Updated prompt"
  }' | jq .

# Stop agent
curl -X POST http://localhost:5000/api/platform/agents/agent-123/stop

# Delete agent
curl -X DELETE http://localhost:5000/api/platform/agents/agent-123
```

#### Agent Invocation

```bash
# Invoke agent (non-streaming)
curl -X POST http://localhost:5000/api/platform/agents/agent-123/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Hello!",
    "context": {"user_id": "user-123"}
  }' | jq .

# Stream chat (SSE)
curl -X POST http://localhost:5000/api/platform/agents/agent-123/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What are my priorities?",
    "session_id": "session-1"
  }'
```

#### Events

```bash
# Fire event
curl -X POST http://localhost:5000/api/platform/events \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cost.exceeded",
    "payload": {"amount": 100},
    "source": "api"
  }'

# Query events
curl "http://localhost:5000/api/events?department=sales" | jq .
```

#### Approvals (HITL)

```bash
# List pending approvals
curl http://localhost:5000/api/approvals | jq .

# Get approval details
curl http://localhost:5000/api/approvals/req-123 | jq .

# Approve
curl -X POST http://localhost:5000/api/approvals/req-123/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approved_by": "user@example.com",
    "reason": "Looks good"
  }'

# Reject
curl -X POST http://localhost:5000/api/approvals/req-123/reject \
  -H "Content-Type: application/json" \
  -d '{
    "rejected_by": "user@example.com",
    "reason": "Insufficient budget"
  }'
```

#### Admin & Metrics

```bash
# Admin metrics
curl http://localhost:5000/api/admin/metrics | jq .

# Admin health
curl http://localhost:5000/api/admin/health | jq .

# Platform overview
curl http://localhost:5000/api/platform/overview | jq .

# Scheduler status
curl http://localhost:5000/api/platform/scheduler | jq .
```

### API Documentation

```bash
# Open Swagger UI in browser
open http://localhost:5000/docs

# Open ReDoc in browser
open http://localhost:5000/redoc

# Get OpenAPI spec
curl http://localhost:5000/openapi.json | jq .
```

---

## Common Workflows

### 1. Deploy and Test a Simple Agent

```bash
# Create agent manifest
cat > my-agent.yaml << 'EOF'
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: my-agent
  description: A simple test agent
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: gpt-4o
    provider: openai
  tools:
    - mcp__filesystem__*
  system_prompt: |
    You are a helpful assistant.
EOF

# Deploy
forgeos deploy my-agent.yaml

# Invoke
forgeos invoke my-agent "Hello!"

# Or with curl
curl -X POST http://localhost:5000/api/platform/agents/my-agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!"}' | jq .
```

### 2. Create a Scheduled Agent

```python
# scheduled_agent.py
from forgeos_sdk import Agent, ForgeOSClient

manifest = (Agent.builder("daily-report")
    .forgeos()
    .scheduled("0 9 * * *")  # 9 AM daily
    .model("gpt-4o")
    .tools("mcp__google-workspace__*", "company__publish_event")
    .prompt("""
        Generate a daily report with:
        1. Key metrics from yesterday
        2. Top 3 action items
        3. Risk alerts
    """)
    .build())

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    print(f"Deployed: {agent_id}")
```

```bash
# Run
PYTHONPATH=. python scheduled_agent.py
```

### 3. Create an Event-Driven Agent

```python
# alert_agent.py
from forgeos_sdk import Agent, ForgeOSClient

manifest = (Agent.builder("alert-responder")
    .forgeos()
    .event_driven("cost.exceeded", "error.critical")
    .model("gpt-4o")
    .tools("company__send_email", "company__create_ticket")
    .prompt("You respond to alerts by notifying the team.")
    .build())

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    
    # Trigger it
    client.fire_event("cost.exceeded", {"amount": 100})
```

### 4. Multi-Agent Workflow

```python
# multi_agent.py
from forgeos_sdk import Agent, ForgeOSClient

# Agent 1: Data collector
collector = (Agent.builder("data-collector")
    .forgeos()
    .reflex()
    .model("gpt-4o")
    .tools("mcp__filesystem__*")
    .prompt("Collect data from files")
    .build())

# Agent 2: Analyzer
analyzer = (Agent.builder("data-analyzer")
    .forgeos()
    .reflex()
    .model("gpt-4o")
    .tools("platform__agent_call")  # Can call other agents
    .prompt("Analyze data and generate insights")
    .build())

with ForgeOSClient() as client:
    collector_id = client.deploy(collector)
    analyzer_id = client.deploy(analyzer)
    
    # Run workflow
    data = client.invoke(collector_id, "Collect sales data")
    insights = client.invoke(analyzer_id, f"Analyze: {data['result']}")
    print(insights["result"])
```

### 5. Chat Agent with Streaming

```python
# chat_stream.py
from forgeos_sdk import Agent, ForgeOSClient

manifest = (Agent.builder("chat-bot")
    .forgeos()
    .reflex()
    .model("claude-sonnet-4-5-20250514")
    .prompt("You are a helpful assistant")
    .build())

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    
    # Stream chat
    print("Agent: ", end="", flush=True)
    for event in client.chat_stream(agent_id, "Tell me a joke", session_id="s1"):
        if event["type"] == "text_delta":
            print(event["content"], end="", flush=True)
    print()
```

### 6. Monitor Platform Health

```bash
# Create a monitoring script
cat > monitor.sh << 'EOF'
#!/bin/bash
while true; do
  echo "=== $(date) ==="
  curl -s http://localhost:5000/api/health | jq '{status, agents: .components.agents_registered, approvals: .components.pending_approvals}'
  sleep 60
done
EOF

chmod +x monitor.sh
./monitor.sh
```

---

## Troubleshooting

### Platform Won't Start

```bash
# Check if port is already in use
lsof -i :5000

# Kill existing process
kill -9 $(lsof -t -i:5000)

# Start with verbose logging
PYTHONPATH=. python3 -m src.bootstrap --dashboard --port 5000 --no-auth
```

### "Connection refused" Error

```bash
# Verify platform is running
curl http://localhost:5000/api/health

# Check the port
export FORGEOS_API_URL=http://localhost:5000

# Or specify in client
client = ForgeOSClient(base_url="http://localhost:5000")
```

### "Simulated response" in Output

```bash
# This means no LLM API key is configured
# Add to .env:
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
# OR
echo 'OPENAI_API_KEY=sk-...' >> .env

# Restart platform
```

### "Agent already exists" Error

```python
# Option 1: Use a different name
manifest = Agent.builder("my-agent-v2").forgeos().build()

# Option 2: Update existing agent
try:
    agent_id = client.deploy(manifest)
except ForgeOSError as e:
    if "already exists" in str(e):
        agents = client.list()
        agent_id = next(a["agent_id"] for a in agents if a["name"] == "my-agent")
        client.update(agent_id, manifest)
```

### Database Connection Issues

```bash
# Use in-memory database (no DATABASE_URL)
unset DATABASE_URL

# Or check PostgreSQL is running
psql -h localhost -U postgres -c "SELECT 1"
```

### View Logs

```bash
# Platform logs are printed to stdout
PYTHONPATH=. python3 -m src.bootstrap --dashboard --port 5000 2>&1 | tee forgeos.log

# Filter for errors
tail -f forgeos.log | grep ERROR
```

### Reset Everything

```bash
# Stop platform
pkill -f "src.bootstrap"

# Clear database (if using PostgreSQL)
psql -h localhost -U postgres -c "DROP DATABASE forgeos; CREATE DATABASE forgeos;"

# Or just restart with in-memory
unset DATABASE_URL
PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000
```

---

## Next Steps

1. **Start the platform**: `PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000`
2. **Deploy your first agent**: `forgeos deploy ./agent.yaml`
3. **Invoke it**: `forgeos invoke <agent-id> "Your prompt"`
4. **Open the dashboard**: http://localhost:3000 (if Next.js dashboard is running)
5. **View API docs**: http://localhost:5000/docs

For more details, see:
- [README.md](README.md) - Platform overview
- [QUICK_START.md](QUICK_START.md) - Quick start guide
- [docs/guides/sdk.md](docs/guides/sdk.md) - SDK documentation
- [docs/reference/api-endpoints.md](docs/reference/api-endpoints.md) - API reference
