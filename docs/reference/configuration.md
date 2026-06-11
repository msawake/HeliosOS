# Configuration Reference

## Environment Variables

All environment variables are loaded from `.env` in the project root at boot time.

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude models | `sk-ant-api03-...` |

At least one LLM provider key is required. Without any key, agents return simulated responses.

### LLM Providers

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | -- |
| `OPENAI_API_KEY` | OpenAI API key (enables gpt-*, o3-* models) | -- |

### Database

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | -- (in-memory) |
| `REDIS_URL` | Redis connection string | -- (in-memory rate limiting) |

When `DATABASE_URL` is not set, all stores use in-memory backends. Data is lost on restart.

Example: `DATABASE_URL=postgresql://user:pass@localhost:5433/forgeos`

### Platform Behavior

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | API server listen port | `5000` |
| `FORGEOS_SKIP_MIGRATIONS` | Skip SQL migrations on boot (`1`, `true`, `yes`) | -- |
| `FORGEOS_SEED_HITL` | Seed demo HITL approvals (`0` to disable) | `1` |
| `FORGEOS_MCP_BOOT_TIMEOUT` | Timeout for MCP server connections at boot (seconds) | `30` |
| `FORGEOS_TOOL_TIMEOUT` | Default timeout for tool execution (seconds) | `60` |
| `FORGEOS_TOOL_MAX_RETRIES` | Max retries for failed tool calls | `2` |
| `FORGEOS_LLM_MAX_RETRIES` | Max retries for LLM API calls | `3` |
| `FORGEOS_LLM_BACKOFF_BASE` | Base delay for LLM retry backoff (seconds) | `1.0` |
| `FORGEOS_LLM_BACKOFF_MAX` | Max delay for LLM retry backoff (seconds) | `30.0` |

### OpenClaw

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENCLAW_DIR` | Path to openclaw2 runtime directory | `./openclaw2` |
| `OPENCLAW_PORT` | Gateway listen port | `18789` |
| `OPENCLAW_STATE_DIR` | Agent workspace storage | `~/.openclaw-forgeos` |

### Dashboard

| Variable | Description | Default |
|----------|-------------|---------|
| `FORGEOS_API_URL` | Backend API URL (set in `dashboard/.env`) | `http://localhost:5000` |

---

## Company Config YAML

Each company has a `config.yaml` under `src/companies/<id>/`. Example: `src/companies/leadforge/config.yaml`.

### Schema

```yaml
company:
  name: "LeadForge AI"
  domain: "leadforge.ai"
  mission: "AI-powered B2B lead generation"

budgets:
  daily_token_budget: 8_000_000
  critical_reserve_pct: 10
  per_session_cost_limit_usd: 50.00
  monthly_infrastructure_budget_usd: 5000
  department_daily_tokens:
    sales: 3_000_000
    marketing: 2_000_000
    finance: 500_000
    hr: 200_000
    legal: 500_000
    operations: 800_000
    executive: 1_000_000

agent_defaults:
  max_retries: 3
  stale_task_threshold_minutes: 60
  checkpoint_interval_minutes: 10
  review_sample_rate: 0.15
  canary_duration_hours: 24

rate_limits:
  max_tool_calls_per_session: 100
  max_api_calls_per_minute: 30
  max_concurrent_agents_per_type: 5

hitl:
  sla:
    financial: 24.0        # hours to resolve
    content: 4.0
    contract: 48.0
    client_agreement: 48.0
    outreach_compliance: 4.0
    ad_spend: 12.0
    data_deletion: 24.0

models:
  orchestrator_default: "claude-opus-4-6"
  doer_default: "claude-sonnet-4-5-20250514"
  classifier_default: "claude-haiku-4-5-20251001"

polling_intervals:
  sdr_outreach_cycle_minutes: 15
  lead_scoring_cycle_minutes: 30
  pipeline_sync_cycle_minutes: 60
  executive_cycle_minutes: 30
  monitoring_cycle_minutes: 2

mcp_servers:
  tier1:
    - name: "filesystem"
      package: "@modelcontextprotocol/server-filesystem"
      required: false
      args:
        - "/Users/jama/Desktop"
        - "/Users/jama/Downloads"
```

### Sections

| Section | Purpose |
|---------|---------|
| `company` | Name, domain, mission statement |
| `budgets` | Token budgets per department, cost limits |
| `agent_defaults` | Default agent behavior (retries, checkpoints) |
| `rate_limits` | Per-session and per-minute limits |
| `hitl` | SLA hours for each approval category |
| `models` | Default model for each agent tier |
| `polling_intervals` | How often scheduled agents run |
| `mcp_servers` | MCP server packages to connect at boot |

---

## LLM Config

Each agent has an `LLMConfig` that determines which model and provider to use.

```python
@dataclass
class LLMConfig:
    chat_model: str = "claude-4-sonnet"
    reasoning_model: str | None = None
    provider: str = "anthropic"
    metadata: dict = field(default_factory=dict)
```

### Model Routing

The LLM Router auto-detects the provider from the model name prefix:

| Prefix | Provider | Examples |
|--------|----------|---------|
| `claude-*` | Anthropic | `claude-sonnet-4-5-20250514`, `claude-opus-4-6` |
| `gpt-*` | OpenAI | `gpt-4o`, `gpt-4o-mini` |
| `o3-*`, `o1-*` | OpenAI | `o3-mini` |

### Failover

Set `metadata.fallback_provider` to enable automatic failover:

```python
LLMConfig(
    chat_model="claude-sonnet-4-5-20250514",
    provider="anthropic",
    metadata={"fallback_provider": "openai"},
)
```

If Anthropic fails after 3 retries, one attempt is made on OpenAI.

---

## Docker Compose

The top-level `docker-compose.yaml` defines three services:

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | `pgvector/pgvector:pg16` | 5433:5432 | Database with pgvector |
| `redis` | `redis:7-alpine` | 6379:6379 | Rate limiting cache |
| `app` | Built from Dockerfile | 5000:5000 | ForgeOS API server |

For local development, start only Postgres (from the repo root):

```bash
docker compose up -d postgres
```

`DB_PASSWORD` defaults to `forgeoslocal`; override it in the project `.env` before the first boot.

---

## Database Migrations

Five SQL migration files in `infrastructure/database/`:

| File | Content |
|------|---------|
| `001_schema.sql` | Core multi-tenant schema (tenants, users, agents, sessions, events, approvals, audit_log) |
| `002_platform_tables.sql` | Platform tables (agent_registry, scheduled_jobs, event_subscriptions) |
| `003_ontology_tables.sql` | Knowledge graph (entities, relationships, signals) |
| `004_client_mcp_configs.sql` | Per-client MCP server configurations |
| `005_audit_log.sql` | No-op (audit_log already in 001) |

Migrations run automatically on boot unless `FORGEOS_SKIP_MIGRATIONS=1`. Each is tracked in a `schema_migrations` table.
