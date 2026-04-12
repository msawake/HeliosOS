# ForgeOS Quick Start Guide

**Version:** 2.0.0  
**Last Updated:** March 26, 2026

---

## What is ForgeOS?

ForgeOS is a platform for running **AI-operated companies** using multi-agent swarms. Think of it as an operating system where AI agents are the workers, organized into departments with clear hierarchies and responsibilities.

### Current Implementation: LeadForge AI

A B2B lead generation agency powered by 26 AI agents across 7 departments:
- **Executive**: CEO, COO, CFO orchestrators
- **Sales**: SDRs, AEs, Researchers, Lead Scorers
- **Marketing**: Content, Email, Google Ads specialists
- **Operations**: Monitoring, Client Success, Vendor Management
- **Finance**: AR, Billing, Reporting
- **HR**: Contractor Management
- **Legal**: Compliance, Contracts

---

## Prerequisites

- **Python**: 3.11 or higher
- **PostgreSQL**: (optional, falls back to in-memory)
- **Redis**: (optional, for rate limiting)
- **API Keys**: Anthropic Claude API key (or OpenAI)

---

## Installation

### 1. Clone and Setup

```bash
cd ~/Documents/one
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Set Environment Variables

```bash
# Required
export ANTHROPIC_API_KEY="your-api-key-here"

# Optional (for production features)
export DATABASE_URL="postgresql://user:pass@localhost/forgeos"
export REDIS_URL="redis://localhost:6379"
export GOOGLE_CLOUD_PROJECT="your-gcp-project"
```

### 3. Run Tests

```bash
pytest
```

---

## Running ForgeOS

### Basic Usage

```bash
# Boot LeadForge AI in supervised mode
python -m src.bootstrap --company leadforge --mode supervised

# Run with dashboard
python -m src.bootstrap --company leadforge --dashboard

# Run with main operational loop
python -m src.bootstrap --company leadforge --dashboard --loop

# Run demo scenario
python -m src.bootstrap --company leadforge --demo
```

### Operating Modes

1. **Shadow Mode** (`--mode shadow`)
   - Agents run but outputs go to review queue
   - No external actions taken
   - Safe for testing

2. **Supervised Mode** (`--mode supervised`) [DEFAULT]
   - Agents execute tasks
   - Humans review before external actions
   - Recommended for production

3. **Autonomous Mode** (`--mode autonomous`)
   - Agents operate independently
   - Humans review daily summaries
   - High-trust mode

### Available Companies

```bash
--company leadforge    # B2B lead generation (default)
--company dealforge    # Sales pipeline management
--company travelforge  # Travel booking
--company insureforge  # Insurance operations
--company homeforge    # Real estate
```

---

## Dashboard

Once running with `--dashboard`, access at:
- **URL**: http://localhost:5000
- **Features**:
  - Approval queue (HITL)
  - Metrics visualization
  - Workflow monitoring
  - Agent activity logs

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    EXECUTIVE LAYER                       │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │   CEO    │    │   COO    │    │   CFO    │          │
│  │ (Opus-4) │    │ (Opus-4) │    │ (Opus-4) │          │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘          │
└───────┼───────────────┼───────────────┼─────────────────┘
        │               │               │
┌───────┼───────────────┼───────────────┼─────────────────┐
│       │        DEPARTMENT LEADS        │                 │
│  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐  ┌──────────┐  │
│  │  Sales  │  │Marketing│  │ Finance │  │Operations│  │
│  │  Lead   │  │  Lead   │  │  Lead   │  │   Lead   │  │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬─────┘  │
└───────┼────────────┼────────────┼────────────┼─────────┘
        │            │            │            │
┌───────┼────────────┼────────────┼────────────┼─────────┐
│       │      SPECIALIST AGENTS (Doers)       │         │
│  ┌────▼────┐  ┌───▼────┐  ┌───▼────┐  ┌────▼─────┐   │
│  │   SDR   │  │Content │  │Billing │  │Monitoring│   │
│  │(Sonnet) │  │(Sonnet)│  │(Sonnet)│  │ (Sonnet) │   │
│  └─────────┘  └────────┘  └────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Key Components

1. **Agent Invoker** - Routes tasks to appropriate agents
2. **Workflow Engine** - DAG-based task orchestration
3. **HITL Gateway** - Human approval workflows
4. **MCP Manager** - External tool integration
5. **Event Bus** - Inter-agent communication
6. **Knowledge Base** - Company policies and data

---

## Configuration

### Global Config: `config/company-config.yaml`

```yaml
company:
  name: "LeadForge AI"
  domain: "leadforge.ai"

budgets:
  daily_token_budget: 10_000_000
  department_daily_tokens:
    sales: 3_000_000
    marketing: 2_500_000

models:
  orchestrator_default: "claude-opus-4-6"
  doer_default: "claude-sonnet-4-5-20250514"
  classifier_default: "claude-haiku-4-5-20251001"

hitl:
  financial_approval_threshold_usd: 1000
  financial_approval_sla_hours: 24
```

### Company-Specific: `src/companies/leadforge/config.yaml`

Override global settings per company.

---

## Development Workflow

### 1. Create a New Company

```bash
# Copy existing company as template
cp -r src/companies/leadforge src/companies/myforge

# Edit configuration
vim src/companies/myforge/config.yaml
vim src/companies/myforge/agent_configs.py
vim src/companies/myforge/workflows.py
vim src/companies/myforge/knowledge.py

# Run it
python -m src.bootstrap --company myforge
```

### 2. Add a New Agent

Edit `src/companies/{company}/agent_configs.py`:

```python
SYSTEM_PROMPTS["my-agent"] = """You are..."""

AGENT_DEFINITIONS.append(
    AgentConfig(
        id="my-agent",
        name="My Agent",
        tier=AgentTier.DOER,
        department="operations",
        system_prompt=SYSTEM_PROMPTS["my-agent"],
        model="claude-sonnet-4-5-20250514",
        tools=["knowledge_base", "metrics"],
    )
)
```

### 3. Create a Workflow

Edit `src/companies/{company}/workflows.py`:

```python
def create_my_workflow(engine, params):
    wf = engine.create_workflow(
        name="My Workflow",
        description="Does something cool"
    )
    
    task1 = engine.add_task(wf, "task-1", "my-agent", "Do step 1")
    task2 = engine.add_task(wf, "task-2", "my-agent", "Do step 2", deps=[task1])
    
    return wf
```

---

## Testing

### Run All Tests

```bash
pytest
```

### Run Specific Test

```bash
pytest tests/test_workflows.py
pytest tests/test_leadforge.py -v
```

### Test Coverage

```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

---

## Deployment

### Local Development (Docker Compose)

```bash
cd infrastructure/docker
docker-compose up -d
```

Services:
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`
- ForgeOS: `localhost:5000`

### Production (GCP)

```bash
cd infrastructure/terraform
terraform init
terraform plan
terraform apply
```

Deploys:
- Cloud SQL (PostgreSQL)
- Cloud Memorystore (Redis)
- Cloud Run (ForgeOS API)
- Cloud Build (CI/CD)

---

## Monitoring

### Metrics

Access via dashboard or query directly:

```python
from src.mcp.custom_tools import CompanySystem

system = CompanySystem(config, company_id="leadforge")
metrics = system.metrics.get_all()
```

### Logs

```bash
# Local
tail -f logs/forgeos.log

# GCP
gcloud logging read "resource.type=cloud_run_revision"
```

### Alerts

Configure in `infrastructure/terraform/monitoring.tf`

---

## Troubleshooting

### Issue: "No API key found"

```bash
export ANTHROPIC_API_KEY="your-key"
# or
export OPENAI_API_KEY="your-key"
```

### Issue: "Database connection failed"

```bash
# Use in-memory mode (no DATABASE_URL)
unset DATABASE_URL

# Or fix connection
export DATABASE_URL="postgresql://user:pass@localhost/forgeos"
```

### Issue: "MCP server failed to connect"

MCP servers are optional. Check `config/company-config.yaml`:

```yaml
mcp_servers:
  tier1:
    - name: "github"
      required: false  # Set to false for optional
```

### Issue: "Agent task failed"

Check logs and HITL dashboard:
1. Review error in dashboard
2. Check agent logs
3. Retry with `--mode shadow` for testing

---

## Common Tasks

### Approve Pending Items

```python
from src.mcp.custom_tools import CompanySystem

system = CompanySystem(config, company_id="leadforge")
pending = system.hitl.get_pending()

# Approve
system.hitl.approve(pending[0]["id"], approver="human@example.com")

# Deny
system.hitl.deny(pending[0]["id"], reason="Not aligned with policy")
```

### Query Knowledge Base

```python
results = system.knowledge.search("lead scoring criteria")
```

### Trigger Workflow

```python
from src.workflows.definitions import WorkflowEngine

engine = WorkflowEngine(invoker=invoker)
wf = create_lead_qualification_workflow(engine, {"lead_id": "123"})
await engine.dispatch(wf)
```

---

## Resources

- **Repository**: ~/Documents/one
- **Review**: See `REPOSITORY_REVIEW.md`
- **Tests**: `tests/`
- **Agent Definitions**: `.claude/agents/`
- **Infrastructure**: `infrastructure/`

---

## Next Steps

1. ✅ Review `REPOSITORY_REVIEW.md` for detailed analysis
2. 📝 Create README.md (see recommendations)
3. 🔧 Set up CI/CD pipeline
4. 📊 Configure monitoring dashboards
5. 🔒 Run security audit
6. 🚀 Deploy to staging environment

---

**Questions?** Check the code comments or review test files for examples.

**Last Updated**: March 26, 2026
