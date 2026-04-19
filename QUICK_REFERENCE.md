# ForgeOS Quick Reference Card

One-page cheat sheet for terminal usage.

---

## 🚀 Start Platform

```bash
# Development mode (no auth, in-memory DB)
PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000

# Production mode (with scheduler)
PYTHONPATH=. python3 -m src.bootstrap --company leadforge --dashboard --loop --port 5000
```

---

## 🔧 CLI Commands

```bash
# Deploy agent
forgeos deploy agent.yaml

# List agents
forgeos list

# Invoke agent
forgeos invoke <agent-id> "Your prompt"

# Get agent details
forgeos get <agent-id>

# Undeploy agent
forgeos undeploy <agent-id>

# Validate manifest
forgeos validate agent.yaml

# Check health
forgeos health
```

---

## 🐍 Python SDK

### Deploy & Invoke

```python
from forgeos_sdk import Agent, ForgeOSClient

# Builder style
manifest = (Agent.builder("my-agent")
    .forgeos()
    .reflex()
    .model("gpt-4o")
    .tools("mcp__filesystem__*")
    .prompt("You are helpful")
    .build())

# Deploy & invoke
with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    result = client.invoke(agent_id, "Hello!")
    print(result["result"])
```

### Class Style

```python
class MyAgent(Agent):
    name = "my-agent"
    stack = "forgeos"
    execution_type = "reflex"
    model = "gpt-4o"
    tools = ["mcp__filesystem__*"]
    system_prompt = "You are helpful"

with ForgeOSClient() as client:
    client.deploy(MyAgent.manifest())
```

---

## 🌐 REST API

### Health & Status

```bash
curl http://localhost:5000/api/health
curl http://localhost:5000/api/readiness
curl http://localhost:5000/metrics
```

### Agent Management

```bash
# List agents
curl http://localhost:5000/api/platform/agents | jq .

# Deploy agent
curl -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "chat_model": "gpt-4o",
    "provider": "openai"
  }' | jq .

# Invoke agent
curl -X POST http://localhost:5000/api/platform/agents/<id>/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!"}' | jq .

# Delete agent
curl -X DELETE http://localhost:5000/api/platform/agents/<id>
```

### Events & Approvals

```bash
# Fire event
curl -X POST http://localhost:5000/api/platform/events \
  -H "Content-Type: application/json" \
  -d '{"name": "cost.exceeded", "payload": {"amount": 100}}'

# List approvals
curl http://localhost:5000/api/approvals | jq .

# Approve
curl -X POST http://localhost:5000/api/approvals/<id>/approve \
  -H "Content-Type: application/json" \
  -d '{"approved_by": "user@example.com"}'
```

---

## 📋 Agent Manifest (YAML)

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: my-agent
  description: What this agent does
  department: operations

spec:
  stack: forgeos                    # forgeos | crewai | adk | openclaw
  execution_type: reflex            # reflex | scheduled | always_on | event_driven | autonomous
  
  schedule: "0 9 * * *"            # For scheduled agents
  event_triggers:                   # For event-driven agents
    - cost.exceeded
  
  llm:
    chat_model: gpt-4o
    provider: openai
  
  tools:
    - mcp__filesystem__*
    - company__publish_event
  
  system_prompt: |
    You are a helpful assistant.
```

---

## 🔑 Execution Types

| Type | When | Example |
|------|------|---------|
| `reflex` | On-demand via API | Chat bot |
| `scheduled` | Cron schedule | Daily report (`0 9 * * *`) |
| `always_on` | Continuous loop | System monitor |
| `event_driven` | Event triggers | Alert responder |
| `autonomous` | Goal-directed | Research agent |

---

## 🛠️ Stack Adapters

| Stack | Description | SDK Required |
|-------|-------------|--------------|
| `forgeos` | Native (default) | None |
| `crewai` | CrewAI framework | `pip install crewai` |
| `adk` | Google ADK | `pip install google-adk` |
| `openclaw` | OpenClaw gateway | Node.js |

---

## 🔐 Authentication

### No Auth (Dev)

```bash
# Start with --no-auth
PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000

# No headers needed
curl http://localhost:5000/api/platform/agents
```

### API Key

```bash
curl http://localhost:5000/api/platform/agents \
  -H "X-API-Key: fos_..."
```

### Bearer Token

```bash
# Get token
TOKEN=$(curl -X POST http://localhost:5000/api/auth/token \
  -d '{"password": "forgeos"}' | jq -r '.token')

# Use token
curl http://localhost:5000/api/platform/agents \
  -H "Authorization: Bearer $TOKEN"
```

---

## 🌍 Environment Variables

```bash
# LLM Providers (at least one)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."

# API Configuration
export PORT=5000
export FORGEOS_API_URL=http://localhost:5000

# Database (optional)
export DATABASE_URL="postgresql://user:pass@localhost/forgeos"

# Redis (optional)
export REDIS_URL="redis://localhost:6379"
```

---

## 📊 Common Patterns

### Scheduled Daily Report

```python
manifest = (Agent.builder("daily-report")
    .forgeos()
    .scheduled("0 9 * * *")
    .model("gpt-4o")
    .tools("mcp__google-workspace__*")
    .prompt("Generate daily report")
    .build())
```

### Event-Driven Alert

```python
manifest = (Agent.builder("alert-responder")
    .forgeos()
    .event_driven("cost.exceeded", "error.critical")
    .model("gpt-4o")
    .tools("company__send_email")
    .prompt("Respond to alerts")
    .build())
```

### Chat with Streaming

```python
with ForgeOSClient() as client:
    for event in client.chat_stream(agent_id, "Hello", session_id="s1"):
        if event["type"] == "text_delta":
            print(event["content"], end="")
```

---

## 🐛 Troubleshooting

```bash
# Check if platform is running
curl http://localhost:5000/api/health

# Kill existing process on port 5000
kill -9 $(lsof -t -i:5000)

# View logs
PYTHONPATH=. python3 -m src.bootstrap --dashboard --port 5000 2>&1 | tee forgeos.log

# Reset everything
pkill -f "src.bootstrap"
unset DATABASE_URL
PYTHONPATH=. python3 -m src.bootstrap --dashboard --no-auth --port 5000
```

---

## 📚 Documentation

- **API Docs**: http://localhost:5000/docs
- **ReDoc**: http://localhost:5000/redoc
- **Dashboard**: http://localhost:3000
- **Full Guide**: [TERMINAL_USAGE_GUIDE.md](TERMINAL_USAGE_GUIDE.md)
- **README**: [README.md](README.md)

---

## 🎯 Quick Examples

### Deploy from YAML

```bash
cat > agent.yaml << 'EOF'
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: test-agent
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: gpt-4o
    provider: openai
  system_prompt: You are helpful
EOF

forgeos deploy agent.yaml
forgeos invoke test-agent "Hello!"
```

### Deploy from Python

```python
from forgeos_sdk import Agent, ForgeOSClient

manifest = Agent.builder("test-agent").forgeos().reflex().model("gpt-4o").build()

with ForgeOSClient() as client:
    agent_id = client.deploy(manifest)
    result = client.invoke(agent_id, "Hello!")
    print(result["result"])
```

### Deploy via cURL

```bash
curl -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "chat_model": "gpt-4o",
    "provider": "openai",
    "system_prompt": "You are helpful"
  }' | jq .

curl -X POST http://localhost:5000/api/platform/agents/test-agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello!"}' | jq .
```

---

## 💡 Pro Tips

1. **Use `--no-auth` for local development** - Skip authentication
2. **Set `FORGEOS_API_URL` env var** - Avoid passing `--url` to CLI
3. **Use `jq` for JSON formatting** - `curl ... | jq .`
4. **Check `/docs` for interactive API** - Swagger UI with try-it-out
5. **Use class-based agents for reusability** - Cleaner than builder pattern
6. **Stream for real-time responses** - Use `chat_stream()` for chat UX
7. **Monitor with `/metrics`** - Prometheus-compatible metrics
8. **Use in-memory DB for testing** - No `DATABASE_URL` needed

---

**Need Help?** See [TERMINAL_USAGE_GUIDE.md](TERMINAL_USAGE_GUIDE.md) for detailed documentation.
