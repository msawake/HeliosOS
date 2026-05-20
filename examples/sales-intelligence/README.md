# Sales Intelligence Platform

Enterprise Sales Intelligence Platform built on ForgeOS -- 6 agents, 3 frameworks, 5 models working together to automate the full B2B sales intelligence pipeline.

## Overview

This example demonstrates ForgeOS's multi-framework orchestration by deploying a complete sales intelligence team where agents from different stacks (ForgeOS, Google ADK, Anthropic Agent SDK) collaborate through the platform's A2A protocol, share state through namespaced boundaries, and operate under unified governance.

## Architecture

```
                    +---------------------+
                    |   sales-manager      |
                    |   (Opus 4.6)         |
                    |   anthropic-agent-sdk|
                    |   supervisor / $15/d |
                    +---------------------+
                       /    |    |    \    \
                      /     |    |     \    \
            +--------+ +--------+ +--------+ +--------+ +--------+
            | market | |research| | lead   | |outreach| |pipeline|
            |monitor | |analyst | |qualfier| |composer| |analyst |
            +--------+ +--------+ +--------+ +--------+ +--------+
            |Gem Flash| |Opus 4.6| |Sonnet  | | GPT-4o | |Gem Pro |
            | ADK     | |Anth SDK| |ForgeOS | |ForgeOS | | ADK    |
            |always_on| |autonom. | | reflex | | reflex | |sched.  |
            | $3/d    | | $10/d  | | $5/d   | | $3/d   | | $2/d   |
            +--------+ +--------+ +--------+ +--------+ +--------+
```

## Agents

| Agent | Stack | Model | Execution | Budget | Role |
|-------|-------|-------|-----------|--------|------|
| **sales-manager** | anthropic-agent-sdk | claude-opus-4-6 | reflex | $15/day | Supervisor -- coordinates all agents, escalation authority |
| **market-monitor** | adk | gemini-2.0-flash | always_on | $3/day | Continuous market scanning, competitor tracking |
| **research-analyst** | anthropic-agent-sdk | claude-opus-4-6 | autonomous | $10/day | Deep research with checkpoints, phased analysis |
| **lead-qualifier** | forgeos | claude-sonnet-4-5 | reflex | $5/day | BANT scoring, SQL classification |
| **outreach-composer** | forgeos | gpt-4o | reflex | $3/day | Email drafting with human approval gate |
| **pipeline-analyst** | adk | gemini-2.5-pro | scheduled | $2/day | Morning pipeline briefs (M-F 8am) |

**Total daily budget: $38.00** (sum of all agents)

## Frameworks

- **ForgeOS** -- Native agentic loop (lead-qualifier, outreach-composer)
- **Google ADK** -- Google AI agent framework (market-monitor, pipeline-analyst)
- **Anthropic Agent SDK** -- Claude-native agents (research-analyst, sales-manager)

## Models

1. **claude-opus-4-6** (Anthropic) -- Deep reasoning for research and supervision
2. **claude-sonnet-4-5** (Anthropic) -- Fast, accurate lead scoring
3. **gpt-4o** (OpenAI) -- Creative email composition
4. **gemini-2.0-flash** (Google) -- High-throughput market monitoring
5. **gemini-2.5-pro** (Google) -- Analytical pipeline forecasting

## Demo Scenes

Seven scenarios demonstrate the full range of ForgeOS capabilities:

### Scene 1: Cold Start Deploy

Deploy the entire team from a single manifest.

```bash
forgeos deploy team.yaml
```

**Demonstrates:** Manifest parsing, multi-stack adapter initialization, agent process creation, namespace registration, budget allocation, policy loading.

### Scene 2: Lead Qualification Pipeline

A new lead arrives. The lead-qualifier scores it using BANT, calls market-monitor for context via A2A, and for borderline leads (score 60-70) dispatches an async research request to research-analyst.

**Demonstrates:** A2A sync calls (`agent__call`), A2A async calls (`agent__async_call`), cross-stack communication (ForgeOS -> ADK), reflex execution, data boundary enforcement.

### Scene 3: Supervised Outreach

The outreach-composer drafts an email but cannot send it directly (`company__send_email` is in the denied tools list). It submits for human approval via `company__request_approval`. The sales-manager monitors the approval flow.

**Demonstrates:** Capability denial (tool blocklist), human-in-the-loop approval gates, policy enforcement (email_daily_limit), CAN-SPAM compliance, audit trail.

### Scene 4: Budget Exhaustion and Recovery

The research-analyst hits its $10/day budget mid-task. The kernel blocks further LLM calls. The sales-manager is notified, reviews the partial work, and either grants a budget extension or reprioritizes.

**Demonstrates:** Per-agent budget enforcement, quota admission checks, supervisor escalation, budget reservation (`reserve_budget`), graceful degradation.

### Scene 5: Cross-Namespace Data Access

The research-analyst needs financial data (finance namespace) for a deal analysis but is restricted to `[sales, marketing]`. The sales-manager grants a temporary capability token scoped to `finance:read` with a 1-hour TTL.

**Demonstrates:** Capability tokens (opaque grants with expiry), namespace boundaries, temporary privilege escalation, capability revocation, PII policy enforcement.

### Scene 6: Signal-Based Task Interruption

The sales-manager sends SIGTERM to the research-analyst mid-task because priorities changed. The research-analyst checkpoints its current state and stops gracefully. Later, it resumes from the checkpoint.

**Demonstrates:** Process signals (SIGTERM/SIGSTOP/SIGCONT), checkpoint/restore, durable state, process lifecycle management, preemption.

### Scene 7: Morning Pipeline Brief

At 8am M-F, the pipeline-analyst wakes up on schedule, queries the CRM, calls lead-qualifier for recent scores, generates a forecast, and publishes the brief via `company__publish_event`.

**Demonstrates:** Scheduled execution (cron), cross-agent data aggregation, event publishing, metric recording, time-based lifecycle.

## Policies

### email_daily_limit

Located at `policies/email-daily-limit.json`. Enforces CAN-SPAM compliance by capping outreach emails at 50 per day. Applied to the outreach-composer agent via the governance section of its manifest.

## Deployment

### Prerequisites

- ForgeOS platform running (`python3 -m src.bootstrap`)
- API keys configured in `.env` for Anthropic, OpenAI, and Google AI
- Python 3.11+

### Deploy

```bash
# Deploy the full team
forgeos deploy team.yaml

# Verify all agents are running
forgeos list --namespace sales

# Check health
forgeos health
```

### Invoke

```bash
# Trigger lead qualification
forgeos invoke lead-qualifier "Qualify lead: Acme Corp, $50K deal, VP of Engineering, Q3 timeline"

# Ask sales-manager to coordinate
forgeos invoke sales-manager "Run a full pipeline review for this week"

# Trigger research
forgeos invoke research-analyst "Deep dive on Acme Corp: financials, tech stack, recent news"
```

### Teardown

```bash
forgeos undeploy sales-manager
forgeos undeploy market-monitor
forgeos undeploy research-analyst
forgeos undeploy lead-qualifier
forgeos undeploy outreach-composer
forgeos undeploy pipeline-analyst
```

## File Structure

```
sales-intelligence/
  team.yaml                       # Team manifest (6 agents)
  policies/
    email-daily-limit.json        # CAN-SPAM daily email cap
  README.md                       # This file
  CAPABILITIES.md                 # Full capability coverage matrix
```

## Capability Coverage

See [CAPABILITIES.md](CAPABILITIES.md) for the full matrix showing which ForgeOS capability is exercised by which agent in which demo scene.
