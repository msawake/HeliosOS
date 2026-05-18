# ForgeOS Product Roadmap

This roadmap outlines the phased development of ForgeOS, transitioning from a core orchestration prototype to an enterprise-grade autonomous operations platform.

---

### Phase 0: Technical Foundation & Multi-Tenant Core
**Goal:** Establish the infrastructure for secure, multi-tenant agent orchestration.

#### Platform Infrastructure (US-1.1)
- [x] **Multi-tenant Database Shell**
    - PostgreSQL with RLS and initial schema migrations.
- [x] **LLM Router Core**
    - Basic Anthropic/OpenAI routing and failover logic.
- [x] **Auth & Tenant Management**
    - Firebase JWT integration and tenant onboarding API.

#### Core Governance (US-1.2)
- [x] **Hook Chain Implementation**
    - First 4 checks: Budget, Rate Limit, Auth, Cost tracking.
- [x] **Audit Log Store**
    - Persistent event logging for all platform actions.

---

### Phase 1: MVP - Quick Wins & Connectivity
**Goal:** Enable background automation for scheduled and event-driven tasks.
*Source of truth: USM Release 1*

#### Agent Execution (US-1.3)
- [x] **Scheduler Engine**
    - Cron-based task execution for `scheduled` agents.
- [x] **Event Bus (Pub/Sub)**
    - Inter-agent messaging for `event_driven` patterns.
- [x] **Native ForgeOS Adapter**
    - Full tool access loop with multi-turn reasoning.

#### Tooling & Connectivity (US-1.4)
- [x] **MCP Server Manager**
    - Lifecycle management for stdio MCP servers.
- [x] **Initial Toolset**
    - CRM, Google Workspace, and Search knowledge tools.

---

### Phase 2: Trust, Governance & Commercialization
**Goal:** Establish human-in-the-loop controls and multi-tenant isolation.
*Source of truth: USM Release 2*

#### Trust & Safety (US-2.1)
- [ ] **HITL Gateway & Dashboard**
    - Approval queue for high-risk agent actions.
- [ ] **Compliance & Content Checker**
    - Automated outbound validation and PII filtering.
- [ ] **Alert Dispatcher**
    - Slack/PagerDuty integration for system and agent failures.

#### Scale & Billing (US-2.2)
- [ ] **Multi-tenant MCP Isolation**
    - Isolated tool contexts per client with dynamic mounting.
- [ ] **Usage Enforcer & Stripe Billing**
    - Token quotas per tenant and automated subscription management.
- [ ] **Multi-Stack Expansion**
    - Full adapters for CrewAI and Google ADK.

---

### Phase 3: Enterprise Intelligence & Workflows
**Goal:** Complex task orchestration and AI-assisted agent design.
*Source of truth: USM Release 3*

#### Advanced Orchestration (US-3.1)
- [ ] **DAG Workflow Engine**
    - Multi-step, multi-agent task orchestration with retries.
- [ ] **Autonomous Goal Execution**
    - Refined loop for high-level objective completion without manual steps.
- [ ] **Enterprise Adapters**
    - OpenClaw integration and custom enterprise adapter support.

#### Intelligence & Design (US-3.2)
- [ ] **Agent Wizard & Planner**
    - Natural language interface for designing and deploying complex agents.
- [ ] **Ontology & Skill Integration**
    - Grounding agent reasoning in the Skill Library (230+ skills).
- [ ] **Advanced Monitoring & ROI Dashboard**
    - Visualization of cost savings, accuracy, and workflow throughput.
