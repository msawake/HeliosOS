# Playwright E2E Suite — Design Spec

**Date:** 2026-05-18
**Status:** Approved
**Scope:** Root-level `e2e/` folder, covering all 24 dashboard pages across 14 feature domains.

---

## 1. Goal

Define and implement a production-grade Playwright end-to-end test suite that verifies every user-facing use case of the ForgeOS platform — the way a QA expert would. The suite must be:

- **Discoverable** by both humans and AI agents (POM structure, one file per page, predictable locations)
- **Maintainable** — single source of truth for every selector; UI changes require updating one file
- **Safe** — no external LLM or third-party service calls; zero cost per run
- **Hermetic** — each test creates and destroys its own backend state; no cross-test pollution

---

## 2. Runtime Architecture

### Services required

| Service | Port | Start command |
|---|---|---|
| FastAPI backend | 5000 | `PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000` |
| Next.js dashboard | 3000 | `cd dashboard && npm run dev` |

`playwright.config.ts` declares both as `webServer` entries so `npx playwright test` auto-starts them. Tests wait for both to be healthy before any spec runs.

**No API keys set → LLM auto-simulation.** The `LLMRouter` falls back to `[Simulated provider/model] Processed N message(s).` when `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are absent. All invoke and chat flows work end-to-end with zero cost.

### Auth strategy

A `globalSetup` script logs in once via `POST /api/auth/token` (default dev password: `forgeos`), saves the session to `e2e/.auth/state.json`. All specs load that state via `storageState` in `playwright.config.ts`.

The single exception: `specs/auth/login.spec.ts` runs without stored state to test the login surface.

### Route intercepts (targeted mocks)

Two endpoints are intercepted to prevent accidental external calls:

| Endpoint | Mock response |
|---|---|
| `POST /api/intelligence/connectors/sync` | `202 Accepted { "status": "queued" }` |
| Any MCP server URL pointing to a non-localhost host | `200 { "tools": [] }` |

Everything else hits the real backend.

---

## 3. Folder Structure

```
e2e/
├── playwright.config.ts
├── fixtures/
│   ├── auth.fixture.ts         ← globalSetup: login once, save storageState
│   └── base.fixture.ts         ← test.extend() injecting all page objects
├── pages/
│   ├── BasePage.ts             ← shared helpers used by all page objects
│   ├── LoginPage.ts
│   ├── OverviewPage.ts
│   ├── AgentsPage.ts
│   ├── CreateAgentPage.ts      ← owns 5-step wizard interactions
│   ├── AgentDetailPage.ts      ← invoke, tabs (activity/logs/config), stop, delete
│   ├── AgentChatPage.ts        ← streaming chat, sessions
│   ├── EnvironmentsPage.ts
│   ├── EnvironmentDetailPage.ts
│   ├── WorkflowsPage.ts
│   ├── ApprovalsPage.ts
│   ├── ClientsPage.ts
│   ├── ClientDetailPage.ts     ← MCP servers, client agents
│   ├── AdminHealthPage.ts
│   ├── AdminChatPage.ts
│   ├── IntelligencePage.ts
│   ├── AuditPage.ts
│   └── SchedulerPage.ts
├── specs/                      ← vertical slices mirroring sidebar navigation
│   ├── auth/
│   │   └── login.spec.ts
│   ├── overview/
│   │   └── dashboard.spec.ts
│   ├── agents/
│   │   ├── list.spec.ts
│   │   ├── create.spec.ts
│   │   ├── detail.spec.ts
│   │   └── chat.spec.ts
│   ├── environments/
│   │   ├── list.spec.ts
│   │   └── detail.spec.ts
│   ├── workflows/
│   │   └── list.spec.ts
│   ├── approvals/
│   │   └── queue.spec.ts
│   ├── clients/
│   │   ├── list.spec.ts
│   │   └── detail.spec.ts
│   ├── admin/
│   │   ├── health.spec.ts
│   │   ├── chat.spec.ts
│   │   ├── audit.spec.ts
│   │   └── scheduler.spec.ts
│   └── flows/                  ← cross-cutting multi-page journeys
│       ├── agent-full-lifecycle.spec.ts
│       ├── client-onboarding.spec.ts
│       └── hitl-approval.spec.ts
└── support/
    ├── api-seed.ts             ← HTTP helpers for backend state setup/teardown
    └── route-intercepts.ts     ← route() mocks for connector sync + external MCP
```

**Vertical slicing rationale:** `specs/` mirrors the sidebar navigation, which is the platform's feature map. Navigation-based grouping is unambiguous — there is no gray area about where a given test belongs. The `specs/flows/` folder is the only exception, reserved for journeys that meaningfully span multiple verticals.

---

## 4. Components

### 4.1 BasePage

Shared base class extended by all page objects. Never instantiated directly.

| Method | Behaviour |
|---|---|
| `navigate(path)` | `page.goto(path)`, waits for `networkidle` |
| `waitForToast(text?)` | waits for success/error notification element; optionally asserts text |
| `waitForRequest(method, urlPattern)` | wraps `page.waitForResponse()` with method filter |
| `expectHeading(text)` | asserts `h1` contains text |

### 4.2 Page Objects

One class per page. Each class:
- Receives `page: Page` in constructor
- Declares all locators as private `readonly` fields using `data-testid` first, ARIA roles second, text last — never CSS classes
- Exposes only public methods that represent meaningful user actions or assertions

| Page Object | Key public methods |
|---|---|
| `LoginPage` | `fillPassword(pw)`, `submit()`, `expectError(msg)` |
| `OverviewPage` | `getStatCard(name)`, `expectLiveConnected()`, `expectLiveDisconnected()` |
| `AgentsPage` | `filterByStack(stack)`, `filterByType(type)`, `filterByOwnership(o)`, `clickCreate()`, `openAgent(name)` |
| `CreateAgentPage` | `selectStack(stack)`, `selectExecutionType(type)`, `fillIdentity(opts)`, `configureLLM(opts)`, `reviewAndSubmit()`, `expectValidationError(field)` |
| `AgentDetailPage` | `invoke(prompt)`, `waitForInvokeResult()`, `openTab(name)`, `stop()`, `delete()` |
| `AgentChatPage` | `sendMessage(text)`, `waitForStreamedResponse()`, `newSession()`, `deleteSession(id)` |
| `EnvironmentsPage` | `create(name)`, `openEnvironment(name)`, `expectStatus(name, status)` |
| `EnvironmentDetailPage` | `addAgent(agentId)`, `openLogs()`, `expectAgentListed(name)` |
| `WorkflowsPage` | `expectWorkflowListed(name)`, `openWorkflow(name)` |
| `ApprovalsPage` | `expectEmpty()`, `expectApprovalListed(title)`, `approve(title)`, `deny(title)` |
| `ClientsPage` | `create(id, name)`, `openClient(id)`, `expectClientListed(id)` |
| `ClientDetailPage` | `addMCPServer(cfg)`, `removeMCPServer(name)`, `expectMCPListed(name)`, `expectAgentListed(name)` |
| `AdminHealthPage` | `expectProviderListed(name)`, `getMetricValue(name)` |
| `AdminChatPage` | `sendMessage(text)`, `waitForResponse()` |
| `IntelligencePage` | `ask(question)`, `waitForAnswer()` |
| `AuditPage` | `expectEntryContaining(text)`, `getRowCount()` |
| `SchedulerPage` | `expectJobListed(name)` |

### 4.3 Fixtures

**`auth.fixture.ts`** — `globalSetup` function that:
1. Launches a bare Chromium context (no storageState)
2. POSTs to `/api/auth/token` with `{ password: 'forgeos' }`
3. Saves `storageState` to `e2e/.auth/state.json`

**`base.fixture.ts`** — `test.extend<Fixtures>()` that instantiates every page object and passes it to the test. Specs destructure only what they need:

```typescript
const { agentsPage, createAgentPage } = test;

test('create a reflex agent', async ({ agentsPage, createAgentPage }) => { ... });
```

### 4.4 Support utilities

**`api-seed.ts`** — plain HTTP helpers (no Playwright), used in `beforeEach` / `afterEach`:
- `seedAgent(opts)` → `{ agentId }` — creates agent via `POST /api/platform/agents`
- `deleteAgent(agentId)` — `DELETE /api/platform/agents/:id`
- `seedClient(id, name)` → creates client
- `deleteClient(id)`
- `seedA2HRequest(opts)` → creates human-in-loop request
- `deleteAllE2EEntities()` — bulk delete everything with `__e2e__` name prefix

All seeded entities use the `__e2e__` prefix so a global `afterAll` teardown can clean up even if individual tests fail.

**`route-intercepts.ts`** — Playwright `page.route()` helpers:
- `interceptConnectorSync(page)` — mocks `POST /api/intelligence/connectors/sync` → `202`
- `interceptExternalMCP(page)` — mocks any MCP server URL not pointing to localhost

---

## 5. Test Coverage Matrix

### `specs/auth/login.spec.ts`
- [ ] Unauthenticated visit to `/` redirects to `/login`
- [ ] Wrong password shows inline error, stays on login
- [ ] Correct password (`forgeos`) redirects to `/` (overview)

### `specs/overview/dashboard.spec.ts`
- [ ] Stat cards render: Total Agents, Running, Personal, Shared
- [ ] "Agents by Stack" section lists all 4 stacks
- [ ] "Agents by Execution Type" section lists all 5 types
- [ ] Live indicator shows connected state when backend is up

### `specs/agents/list.spec.ts`
- [ ] Seeded agents appear in the list
- [ ] Filter by stack narrows results
- [ ] Filter by execution type narrows results
- [ ] Filter by ownership narrows results
- [ ] "+ Create Agent" button navigates to `/agents/create`
- [ ] Clicking an agent row navigates to `/agents/[id]`
- [ ] Live status dot reflects running/idle state

### `specs/agents/create.spec.ts`
- [ ] 5-step wizard renders step indicators
- [ ] Happy path: select ForgeOS stack → reflex type → fill name/description → configure LLM → submit → redirects to agent detail
- [ ] Step 3 blocked if name is empty (validation error shown)
- [ ] Selecting `scheduled` execution type reveals schedule field; submitting without it is blocked
- [ ] Selecting `autonomous` execution type reveals goal field; submitting without it is blocked
- [ ] Switching LLM provider updates the default model value
- [ ] AI Wizard page (`/agents/create/ai`) renders and accepts a text prompt
- [ ] AI Wizard generates a create-agent form pre-filled from the prompt (navigates to `/agents/create` with query params)

### `specs/agents/detail.spec.ts`
- [ ] Agent name, stack badge, execution type badge, status badge all rendered
- [ ] Invoke panel: submitting a prompt calls `POST /api/platform/agents/:id/invoke`, result panel appears
- [ ] Activity tab: switches content, entries rendered
- [ ] Logs tab: log text block rendered
- [ ] Config tab: key config fields visible
- [ ] "Stop" button calls `POST /api/platform/agents/:id/stop`, status updates
- [ ] "Delete" button shows confirmation, confirms, calls `DELETE`, redirects to `/agents`

### `specs/agents/chat.spec.ts`
- [ ] Chat UI renders: message input, send button
- [ ] Sending a message triggers `POST /api/platform/agents/:id/chat/stream`
- [ ] Streamed response appears in the conversation
- [ ] Session list shows current session
- [ ] "New session" creates a new session entry
- [ ] Deleting a session removes it from the list

### `specs/environments/list.spec.ts`
- [ ] Environments list renders (empty state graceful)
- [ ] Create form appears on button click
- [ ] Submit with a name → new row appears with `pending` or `running` status badge
- [ ] Status badge colour matches state

### `specs/environments/detail.spec.ts`
- [ ] Environment metadata rendered: name, namespace, resource requests
- [ ] "Add Agent" flow links an agent to the environment
- [ ] Linked agent appears in the agents list
- [ ] "View Logs" opens log panel

### `specs/workflows/list.spec.ts`
- [ ] Page loads without error
- [ ] Workflow rows render with name and status
- [ ] Clicking a workflow navigates to `/workflows/[id]`

### `specs/approvals/queue.spec.ts`
- [ ] Empty state: placeholder text visible, no action buttons
- [ ] Seeded A2H request appears with agent name, category badge, SLA hours
- [ ] "Approve" button calls `POST /api/approvals/:id/approve`, row removed
- [ ] "Deny" button calls `POST /api/approvals/:id/reject`, row removed

### `specs/clients/list.spec.ts`
- [ ] Create form appears on "+ New Client" click
- [ ] Submitting with ID + name calls `POST /api/clients`, new row appears
- [ ] Client rows link to `/clients/[id]`
- [ ] Empty client ID or name blocked (form validation)

### `specs/clients/detail.spec.ts`
- [ ] Client name and ID rendered in heading
- [ ] "Add MCP Server" form submits, server appears in list
- [ ] "Remove" on a server removes it from list
- [ ] Client agents section renders (empty or seeded)

### `specs/admin/health.spec.ts`
- [ ] System health page loads without error
- [ ] At least one provider listed (simulated mode shows "simulated")
- [ ] `GET /metrics` returns `200` with Prometheus text

### `specs/admin/chat.spec.ts`
- [ ] Chat input and send button rendered
- [ ] Sending a message returns a response (simulated)

### `specs/admin/audit.spec.ts`
- [ ] Audit log table renders columns
- [ ] After a seeded agent action, a new entry appears
- [ ] Rows are ordered newest-first

### `specs/admin/scheduler.spec.ts`
- [ ] Scheduler page renders
- [ ] Scheduled jobs list visible (empty state graceful)

### `specs/flows/agent-full-lifecycle.spec.ts`
- [ ] Create agent (ForgeOS, reflex) via the wizard
- [ ] Agent appears in `/agents` list
- [ ] Navigate to agent detail
- [ ] Invoke agent with a prompt, result renders
- [ ] Stop agent, status updates to stopped
- [ ] Delete agent, redirected to list, agent gone

### `specs/flows/client-onboarding.spec.ts`
- [ ] Create new client via the Clients page
- [ ] Navigate to client detail
- [ ] Add an MCP server configuration
- [ ] Deploy an agent scoped to the client (via Create Agent with client context)
- [ ] Agent appears in the client's agent list

### `specs/flows/hitl-approval.spec.ts`
- [ ] Seed an A2H approval request via `api-seed.ts`
- [ ] Navigate to `/approvals`
- [ ] Request appears with correct title and agent name
- [ ] Approve it
- [ ] Approval queue is empty (or request is gone)

---

## 6. Stability & Quality Conventions

**Selector priority** (enforced in all page objects):
1. `data-testid` attributes — must be added to the dashboard during implementation
2. ARIA roles: `getByRole('button', { name: '...' })`
3. Text content: `getByText(...)` — only for read assertions, not interactions
4. Never CSS class selectors

**Flakiness guards:**
- Streaming chat: assert response container is non-empty (don't assert exact simulated text)
- WebSocket live indicator: wait for `connected` state before asserting counts
- After form submission, always `waitForRequest` on the expected API call before asserting UI change
- Timeouts: default 10 s per action, 15 s for streaming responses

**Failure artefacts:**
- `screenshot: 'only-on-failure'`
- `trace: 'on-first-retry'`
- Output directory: `e2e/test-results/`

**State isolation:**
- Every `beforeEach` that seeds state registers a matching `afterEach` cleanup
- All seeded entity names start with `__e2e__`
- A `globalTeardown` script calls `deleteAllE2EEntities()` as a safety net

---

## 7. Running the Suite

```bash
# Install (from repo root)
cd e2e && npm install

# Run all specs (auto-starts backend + dashboard)
npx playwright test

# Run a single vertical
npx playwright test specs/agents/

# Run only the cross-cutting flows
npx playwright test specs/flows/

# Run headed (watch mode)
npx playwright test --headed

# Open last trace on failure
npx playwright show-trace e2e/test-results/**/trace.zip
```

---

## 8. `data-testid` additions required in dashboard

The following dashboard components need `data-testid` attributes added during implementation. This is part of the implementation plan.

| Component | Attribute needed |
|---|---|
| Sidebar nav links | `data-testid="nav-{label}"` |
| Stat cards | `data-testid="stat-{name}"` |
| Agent list rows | `data-testid="agent-row-{id}"` |
| Create Agent steps | `data-testid="wizard-step-{n}"` |
| Invoke panel | `data-testid="invoke-panel"`, `data-testid="invoke-result"` |
| Approval rows | `data-testid="approval-row-{id}"` |
| Client rows | `data-testid="client-row-{id}"` |
| Toast notifications | `data-testid="toast"` |
| Live status indicator | `data-testid="live-indicator"` |
| Tab buttons | `data-testid="tab-{name}"` |

---

## 9. Out of Scope

- Mobile / responsive testing (Playwright projects for mobile viewports can be added later)
- Visual regression / screenshot diffing
- Performance / load testing
- Testing the Python SDK CLI (`forgeos deploy`, `forgeos invoke`)
- Direct API contract testing (covered by `tests/conformance/`)
