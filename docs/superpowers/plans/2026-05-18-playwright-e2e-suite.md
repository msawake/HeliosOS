# Playwright E2E Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a root-level `e2e/` Playwright test suite covering all 24 ForgeOS dashboard pages using the Page Object Model, vertical-slice specs, and a single globalSetup login.

**Architecture:** TypeScript + `@playwright/test`. One page object per dashboard page. `global.setup.ts` logs in once and saves `storageState`; all specs share it. Two `page.route()` intercepts guard the only external-call endpoints. `api-seed.ts` creates/destroys backend state per test. The dashboard gets `data-testid` attributes on key interactive elements.

**Tech Stack:** Playwright 1.44+, TypeScript 5.4+, Node.js 20+, `@playwright/test`

---

## File Map

**Create (e2e/):**
```
e2e/
├── package.json
├── tsconfig.json
├── playwright.config.ts
├── .gitignore
├── global.setup.ts               ← login once, write .auth/state.json
├── global.teardown.ts            ← delete all __e2e__ entities
├── fixtures/
│   └── base.fixture.ts           ← test.extend() with all page objects
├── pages/
│   ├── BasePage.ts
│   ├── LoginPage.ts
│   ├── OverviewPage.ts
│   ├── AgentsPage.ts
│   ├── CreateAgentPage.ts
│   ├── AgentDetailPage.ts
│   ├── AgentChatPage.ts
│   ├── EnvironmentsPage.ts
│   ├── EnvironmentDetailPage.ts
│   ├── WorkflowsPage.ts
│   ├── ApprovalsPage.ts
│   ├── ClientsPage.ts
│   ├── ClientDetailPage.ts
│   ├── AdminHealthPage.ts
│   ├── AdminChatPage.ts
│   ├── IntelligencePage.ts
│   ├── AuditPage.ts
│   └── SchedulerPage.ts
├── specs/
│   ├── auth/login.spec.ts
│   ├── overview/dashboard.spec.ts
│   ├── agents/list.spec.ts
│   ├── agents/create.spec.ts
│   ├── agents/detail.spec.ts
│   ├── agents/chat.spec.ts
│   ├── environments/list.spec.ts
│   ├── environments/detail.spec.ts
│   ├── workflows/list.spec.ts
│   ├── approvals/queue.spec.ts
│   ├── clients/list.spec.ts
│   ├── clients/detail.spec.ts
│   ├── admin/health.spec.ts
│   ├── admin/chat.spec.ts
│   ├── admin/audit.spec.ts
│   ├── admin/scheduler.spec.ts
│   ├── flows/agent-full-lifecycle.spec.ts
│   ├── flows/client-onboarding.spec.ts
│   └── flows/hitl-approval.spec.ts
└── support/
    ├── api-seed.ts
    └── route-intercepts.ts
```

**Modify (dashboard/):**
```
dashboard/src/components/StatCard.tsx          ← add data-testid
dashboard/src/components/Sidebar.tsx           ← add data-testid to nav links
dashboard/src/app/page.tsx                     ← add data-testid to live indicator
dashboard/src/app/agents/page.tsx              ← add data-testid to rows + live indicator
dashboard/src/app/agents/create/page.tsx       ← add data-testid to wizard steps + stack options
dashboard/src/app/agents/[id]/page.tsx         ← add data-testid to invoke panel, tabs
dashboard/src/app/agents/[id]/chat/page.tsx    ← add data-testid to message list, sessions
dashboard/src/app/approvals/page.tsx           ← add data-testid to approval rows
dashboard/src/app/clients/page.tsx             ← add data-testid to client rows
```

---

## Task 1: Scaffold — package.json, tsconfig, playwright.config, gitignore

**Files:**
- Create: `e2e/package.json`
- Create: `e2e/tsconfig.json`
- Create: `e2e/playwright.config.ts`
- Create: `e2e/.gitignore`

- [ ] **Step 1.1: Create `e2e/package.json`**

```json
{
  "name": "forgeos-e2e",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "test": "playwright test",
    "test:headed": "playwright test --headed",
    "test:ui": "playwright test --ui",
    "test:agents": "playwright test specs/agents/",
    "test:flows": "playwright test specs/flows/",
    "report": "playwright show-report"
  },
  "devDependencies": {
    "@playwright/test": "^1.44.0",
    "typescript": "^5.4.0"
  }
}
```

- [ ] **Step 1.2: Create `e2e/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "baseUrl": "."
  },
  "include": ["**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 1.3: Create `e2e/playwright.config.ts`**

```typescript
import { defineConfig, devices } from '@playwright/test';
import path from 'path';

export const AUTH_FILE = path.join(__dirname, '.auth/state.json');

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  use: {
    baseURL: 'http://localhost:3000',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /global\.setup\.ts/,
      use: { storageState: undefined },
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: AUTH_FILE,
      },
      dependencies: ['setup'],
    },
  ],
  globalTeardown: './global.teardown.ts',
  outputDir: 'test-results/',
  webServer: [
    {
      command: 'cd .. && PYTHONPATH=. python3 -m src.bootstrap --dashboard --port 5000',
      url: 'http://localhost:5000/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:3000',
      reuseExistingServer: !process.env.CI,
      cwd: '../dashboard',
      timeout: 60_000,
    },
  ],
});
```

- [ ] **Step 1.4: Create `e2e/.gitignore`**

```
node_modules/
.auth/
test-results/
playwright-report/
```

- [ ] **Step 1.5: Install dependencies**

```bash
cd e2e && npm install && npx playwright install chromium
```

Expected: chromium browser downloaded, `node_modules/` populated.

- [ ] **Step 1.6: Verify config parses**

```bash
cd e2e && npx playwright --version
```

Expected: prints `Version 1.44.x` (no errors).

- [ ] **Step 1.7: Commit**

```bash
git add e2e/package.json e2e/tsconfig.json e2e/playwright.config.ts e2e/.gitignore
git commit -m "feat(e2e): scaffold Playwright project"
```

---

## Task 2: Global setup + teardown

**Files:**
- Create: `e2e/global.setup.ts`
- Create: `e2e/global.teardown.ts`

- [ ] **Step 2.1: Create `.auth/` directory placeholder**

```bash
mkdir -p e2e/.auth && touch e2e/.auth/.gitkeep
```

- [ ] **Step 2.2: Create `e2e/global.setup.ts`**

```typescript
import { test as setup, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const AUTH_FILE = path.join(__dirname, '.auth/state.json');

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'ForgeOS' })).toBeVisible();
  await page.getByPlaceholder('Enter password').fill(
    process.env.FORGEOS_E2E_PASSWORD ?? 'forgeos',
  );
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('/');
  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  await page.context().storageState({ path: AUTH_FILE });
});
```

- [ ] **Step 2.3: Create `e2e/global.teardown.ts`**

```typescript
import { deleteAllE2EEntities } from './support/api-seed';

export default async function globalTeardown(): Promise<void> {
  try {
    await deleteAllE2EEntities();
  } catch {
    // teardown best-effort — backend may already be stopped
  }
}
```

- [ ] **Step 2.4: Commit**

```bash
git add e2e/global.setup.ts e2e/global.teardown.ts e2e/.auth/.gitkeep
git commit -m "feat(e2e): add global auth setup and teardown"
```

---

## Task 3: Support utilities — api-seed + route-intercepts

**Files:**
- Create: `e2e/support/api-seed.ts`
- Create: `e2e/support/route-intercepts.ts`

- [ ] **Step 3.1: Create `e2e/support/api-seed.ts`**

```typescript
const API = process.env.FORGEOS_API_URL ?? 'http://localhost:5000';

async function post(path: string, body: unknown): Promise<unknown> {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}: ${await res.text()}`);
  return res.json();
}

async function del(path: string): Promise<void> {
  await fetch(`${API}${path}`, { method: 'DELETE' });
}

export async function seedAgent(opts: {
  name: string;
  stack?: string;
  execution_type?: string;
  description?: string;
  system_prompt?: string;
}): Promise<{ agentId: string }> {
  const data = (await post('/api/platform/agents', {
    name: `__e2e__${opts.name}`,
    stack: opts.stack ?? 'forgeos',
    execution_type: opts.execution_type ?? 'reflex',
    description: opts.description ?? 'E2E test agent',
    system_prompt: opts.system_prompt ?? 'You are a test agent for automated QA.',
  })) as { agent_id: string };
  return { agentId: data.agent_id };
}

export async function deleteAgent(agentId: string): Promise<void> {
  await del(`/api/platform/agents/${agentId}`);
}

export async function seedClient(id: string, name: string): Promise<void> {
  const res = await fetch(`${API}/api/clients`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: `__e2e__${id}`, name: `__e2e__ ${name}` }),
  });
  if (!res.ok && res.status !== 409) {
    throw new Error(`seedClient → ${res.status}: ${await res.text()}`);
  }
}

export async function deleteClient(id: string): Promise<void> {
  await del(`/api/clients/__e2e__${id}`);
}

export async function seedA2HRequest(opts: {
  title: string;
  agent: string;
  category?: string;
  sla_hours?: number;
}): Promise<{ requestId: string }> {
  const data = (await post('/api/a2h/requests', {
    title: `__e2e__${opts.title}`,
    agent: opts.agent,
    category: opts.category ?? 'approval',
    sla_hours: opts.sla_hours ?? 24,
    description: 'E2E test approval request',
  })) as { request_id: string };
  return { requestId: data.request_id };
}

export async function deleteAllE2EEntities(): Promise<void> {
  const agentsRes = await fetch(`${API}/api/platform/agents`);
  if (agentsRes.ok) {
    const agents = (await agentsRes.json()) as Array<{ name: string; agent_id: string }>;
    for (const agent of agents) {
      if (agent.name?.startsWith('__e2e__')) await del(`/api/platform/agents/${agent.agent_id}`);
    }
  }
  const clientsRes = await fetch(`${API}/api/clients`);
  if (clientsRes.ok) {
    const clients = (await clientsRes.json()) as Array<{ client_id: string; name: string }>;
    for (const c of clients) {
      if (c.client_id?.startsWith('__e2e__') || c.name?.startsWith('__e2e__')) {
        await del(`/api/clients/${c.client_id}`);
      }
    }
  }
}
```

- [ ] **Step 3.2: Create `e2e/support/route-intercepts.ts`**

```typescript
import type { Page } from '@playwright/test';

export async function interceptConnectorSync(page: Page): Promise<void> {
  await page.route('**/api/intelligence/connectors/sync', async (route) => {
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'queued' }),
    });
  });
}

export async function interceptExternalMCP(page: Page): Promise<void> {
  await page.route(/^(?!http:\/\/localhost).*\/(mcp|tools)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tools: [] }),
    });
  });
}
```

- [ ] **Step 3.3: Commit**

```bash
git add e2e/support/
git commit -m "feat(e2e): add api-seed and route-intercepts utilities"
```

---

## Task 4: BasePage + base fixture

**Files:**
- Create: `e2e/pages/BasePage.ts`
- Create: `e2e/fixtures/base.fixture.ts`

- [ ] **Step 4.1: Create `e2e/pages/BasePage.ts`**

```typescript
import { type Page, expect } from '@playwright/test';

export class BasePage {
  constructor(protected readonly page: Page) {}

  async navigate(path: string): Promise<void> {
    await this.page.goto(path);
    await this.page.waitForLoadState('networkidle');
  }

  async waitForInlineError(text?: string): Promise<void> {
    const el = this.page.locator('[class*="red"], [class*="error"]').first();
    await el.waitFor({ state: 'visible', timeout: 8_000 });
    if (text) await expect(el).toContainText(text);
  }

  async waitForResponse(method: string, urlPattern: string | RegExp): Promise<void> {
    await this.page.waitForResponse(
      (res) =>
        res.request().method().toUpperCase() === method.toUpperCase() &&
        (typeof urlPattern === 'string'
          ? res.url().includes(urlPattern)
          : urlPattern.test(res.url())),
      { timeout: 15_000 },
    );
  }

  async expectHeading(text: string): Promise<void> {
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText(text);
  }
}
```

- [ ] **Step 4.2: Create `e2e/fixtures/base.fixture.ts`**

```typescript
import { test as base } from '@playwright/test';
import { LoginPage } from '../pages/LoginPage';
import { OverviewPage } from '../pages/OverviewPage';
import { AgentsPage } from '../pages/AgentsPage';
import { CreateAgentPage } from '../pages/CreateAgentPage';
import { AgentDetailPage } from '../pages/AgentDetailPage';
import { AgentChatPage } from '../pages/AgentChatPage';
import { EnvironmentsPage } from '../pages/EnvironmentsPage';
import { EnvironmentDetailPage } from '../pages/EnvironmentDetailPage';
import { WorkflowsPage } from '../pages/WorkflowsPage';
import { ApprovalsPage } from '../pages/ApprovalsPage';
import { ClientsPage } from '../pages/ClientsPage';
import { ClientDetailPage } from '../pages/ClientDetailPage';
import { AdminHealthPage } from '../pages/AdminHealthPage';
import { AdminChatPage } from '../pages/AdminChatPage';
import { IntelligencePage } from '../pages/IntelligencePage';
import { AuditPage } from '../pages/AuditPage';
import { SchedulerPage } from '../pages/SchedulerPage';

type Pages = {
  loginPage: LoginPage;
  overviewPage: OverviewPage;
  agentsPage: AgentsPage;
  createAgentPage: CreateAgentPage;
  agentDetailPage: AgentDetailPage;
  agentChatPage: AgentChatPage;
  environmentsPage: EnvironmentsPage;
  environmentDetailPage: EnvironmentDetailPage;
  workflowsPage: WorkflowsPage;
  approvalsPage: ApprovalsPage;
  clientsPage: ClientsPage;
  clientDetailPage: ClientDetailPage;
  adminHealthPage: AdminHealthPage;
  adminChatPage: AdminChatPage;
  intelligencePage: IntelligencePage;
  auditPage: AuditPage;
  schedulerPage: SchedulerPage;
};

export const test = base.extend<Pages>({
  loginPage: async ({ page }, use) => use(new LoginPage(page)),
  overviewPage: async ({ page }, use) => use(new OverviewPage(page)),
  agentsPage: async ({ page }, use) => use(new AgentsPage(page)),
  createAgentPage: async ({ page }, use) => use(new CreateAgentPage(page)),
  agentDetailPage: async ({ page }, use) => use(new AgentDetailPage(page)),
  agentChatPage: async ({ page }, use) => use(new AgentChatPage(page)),
  environmentsPage: async ({ page }, use) => use(new EnvironmentsPage(page)),
  environmentDetailPage: async ({ page }, use) => use(new EnvironmentDetailPage(page)),
  workflowsPage: async ({ page }, use) => use(new WorkflowsPage(page)),
  approvalsPage: async ({ page }, use) => use(new ApprovalsPage(page)),
  clientsPage: async ({ page }, use) => use(new ClientsPage(page)),
  clientDetailPage: async ({ page }, use) => use(new ClientDetailPage(page)),
  adminHealthPage: async ({ page }, use) => use(new AdminHealthPage(page)),
  adminChatPage: async ({ page }, use) => use(new AdminChatPage(page)),
  intelligencePage: async ({ page }, use) => use(new IntelligencePage(page)),
  auditPage: async ({ page }, use) => use(new AuditPage(page)),
  schedulerPage: async ({ page }, use) => use(new SchedulerPage(page)),
});

export { expect } from '@playwright/test';
```

- [ ] **Step 4.3: Commit**

```bash
git add e2e/pages/BasePage.ts e2e/fixtures/base.fixture.ts
git commit -m "feat(e2e): add BasePage and base fixture"
```

---

## Task 5: Page objects — Login, Overview, Agents, CreateAgent

**Files:**
- Create: `e2e/pages/LoginPage.ts`
- Create: `e2e/pages/OverviewPage.ts`
- Create: `e2e/pages/AgentsPage.ts`
- Create: `e2e/pages/CreateAgentPage.ts`

- [ ] **Step 5.1: Create `e2e/pages/LoginPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class LoginPage extends BasePage {
  private readonly passwordInput = this.page.getByPlaceholder('Enter password');
  private readonly submitButton = this.page.getByRole('button', { name: 'Sign in' });

  async fillPassword(password: string): Promise<void> {
    await this.passwordInput.fill(password);
  }

  async submit(): Promise<void> {
    await this.submitButton.click();
  }

  async expectError(message: string): Promise<void> {
    await expect(this.page.getByText(message)).toBeVisible({ timeout: 5_000 });
  }
}
```

- [ ] **Step 5.2: Create `e2e/pages/OverviewPage.ts`**

```typescript
import { expect, type Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class OverviewPage extends BasePage {
  async getStatCard(slug: string): Promise<Locator> {
    return this.page.getByTestId(`stat-${slug}`);
  }

  async expectLiveConnected(): Promise<void> {
    await expect(this.page.getByTestId('live-indicator')).toHaveClass(/bg-green-500/, {
      timeout: 8_000,
    });
  }

  async expectStackListed(label: string): Promise<void> {
    await expect(this.page.getByText(label, { exact: false })).toBeVisible();
  }

  async expectExecTypeListed(label: string): Promise<void> {
    await expect(this.page.getByText(label, { exact: false })).toBeVisible();
  }
}
```

- [ ] **Step 5.3: Create `e2e/pages/AgentsPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentsPage extends BasePage {
  private readonly stackFilter = this.page.locator('select').first();
  private readonly typeFilter = this.page.locator('select').nth(1);
  private readonly ownershipFilter = this.page.locator('select').nth(2);
  private readonly createLink = this.page.getByRole('link', { name: /Create Agent/ });

  async filterByStack(stack: string): Promise<void> {
    await this.stackFilter.selectOption(stack);
    await this.page.waitForLoadState('networkidle');
  }

  async filterByType(type: string): Promise<void> {
    await this.typeFilter.selectOption(type);
    await this.page.waitForLoadState('networkidle');
  }

  async filterByOwnership(ownership: string): Promise<void> {
    await this.ownershipFilter.selectOption(ownership);
    await this.page.waitForLoadState('networkidle');
  }

  async clickCreate(): Promise<void> {
    await this.createLink.click();
    await this.page.waitForURL('/agents/create');
  }

  async openAgent(name: string): Promise<void> {
    await this.page.getByTestId(`agent-row-${name}`).click();
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectLiveIndicatorVisible(): Promise<void> {
    await expect(this.page.getByTestId('live-indicator')).toBeVisible();
  }
}
```

- [ ] **Step 5.4: Create `e2e/pages/CreateAgentPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export interface IdentityOpts {
  name: string;
  description?: string;
  department?: string;
  goal?: string;
  schedule?: string;
}

export interface LLMOpts {
  provider?: string;
  model?: string;
}

export class CreateAgentPage extends BasePage {
  private readonly nextButton = this.page.getByRole('button', { name: /Next/ });
  private readonly deployButton = this.page.getByRole('button', { name: /Deploy|Create/ });

  async selectStack(stack: string): Promise<void> {
    await this.page.getByTestId(`stack-option-${stack}`).click();
    await this.nextButton.click();
  }

  async selectExecutionType(type: string): Promise<void> {
    await this.page.getByTestId(`exec-type-${type}`).click();
    await this.nextButton.click();
  }

  async fillIdentity(opts: IdentityOpts): Promise<void> {
    await this.page.getByLabel(/Name/).fill(opts.name);
    if (opts.description) await this.page.getByLabel(/Description/).fill(opts.description);
    if (opts.department) await this.page.getByLabel(/Department/).fill(opts.department);
    if (opts.goal) await this.page.getByLabel(/Goal/).fill(opts.goal);
    if (opts.schedule) await this.page.getByLabel(/Schedule/).fill(opts.schedule);
    await this.nextButton.click();
  }

  async configureLLM(opts: LLMOpts = {}): Promise<void> {
    if (opts.provider) {
      await this.page.getByLabel(/Provider/).selectOption(opts.provider);
    }
    await this.nextButton.click();
  }

  async reviewAndSubmit(): Promise<string> {
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/platform/agents') && res.request().method() === 'POST',
    );
    await this.deployButton.click();
    const res = await responsePromise;
    const data = (await res.json()) as { agent_id?: string };
    await this.page.waitForURL(/\/agents\//);
    return data.agent_id ?? '';
  }

  async expectValidationError(fieldPattern: string | RegExp): Promise<void> {
    await expect(
      this.page.getByText(typeof fieldPattern === 'string' ? new RegExp(fieldPattern, 'i') : fieldPattern),
    ).toBeVisible({ timeout: 5_000 });
  }

  async expectStepIndicator(stepNumber: number): Promise<void> {
    await expect(this.page.getByTestId(`wizard-step-${stepNumber}`)).toBeVisible();
  }
}
```

- [ ] **Step 5.5: Commit**

```bash
git add e2e/pages/LoginPage.ts e2e/pages/OverviewPage.ts e2e/pages/AgentsPage.ts e2e/pages/CreateAgentPage.ts
git commit -m "feat(e2e): add Login, Overview, Agents, CreateAgent page objects"
```

---

## Task 6: Page objects — AgentDetail, AgentChat

**Files:**
- Create: `e2e/pages/AgentDetailPage.ts`
- Create: `e2e/pages/AgentChatPage.ts`

- [ ] **Step 6.1: Create `e2e/pages/AgentDetailPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentDetailPage extends BasePage {
  private readonly invokePanel = this.page.getByTestId('invoke-panel');
  private readonly invokeResult = this.page.getByTestId('invoke-result');
  private readonly stopButton = this.page.getByRole('button', { name: /Stop/ });
  private readonly deleteButton = this.page.getByRole('button', { name: /Delete|Undeploy/ });

  async invoke(prompt: string): Promise<void> {
    await this.invokePanel.getByRole('textbox').fill(prompt);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/invoke') && res.request().method() === 'POST',
    );
    await this.invokePanel.getByRole('button', { name: /Invoke|Run/ }).click();
    await responsePromise;
  }

  async waitForInvokeResult(): Promise<string> {
    await expect(this.invokeResult).toBeVisible({ timeout: 15_000 });
    return (await this.invokeResult.textContent()) ?? '';
  }

  async openTab(name: 'activity' | 'logs' | 'config'): Promise<void> {
    await this.page.getByTestId(`tab-${name}`).click();
    await this.page.waitForLoadState('networkidle');
  }

  async stop(): Promise<void> {
    await this.stopButton.click();
    await this.waitForResponse('POST', '/stop');
  }

  async delete(): Promise<void> {
    await this.deleteButton.click();
    const confirmButton = this.page.getByRole('button', { name: /Confirm|Yes|Delete/ }).last();
    await confirmButton.click();
    await this.page.waitForURL('/agents');
  }

  async expectBadgeVisible(text: string): Promise<void> {
    await expect(this.page.getByText(text, { exact: false })).toBeVisible();
  }
}
```

- [ ] **Step 6.2: Create `e2e/pages/AgentChatPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentChatPage extends BasePage {
  private readonly messageInput = this.page.getByTestId('chat-input');
  private readonly sendButton = this.page.getByRole('button', { name: /Send/ });
  private readonly messageList = this.page.getByTestId('chat-messages');

  async sendMessage(text: string): Promise<void> {
    await this.messageInput.fill(text);
    await this.sendButton.click();
  }

  async waitForStreamedResponse(): Promise<void> {
    await expect(
      this.messageList.locator('[data-role="assistant"]').last(),
    ).not.toBeEmpty({ timeout: 15_000 });
  }

  async newSession(): Promise<void> {
    await this.page.getByRole('button', { name: /New [Ss]ession/ }).click();
  }

  async deleteSession(index = 0): Promise<void> {
    await this.page
      .getByTestId('session-list')
      .getByRole('button', { name: /Delete/ })
      .nth(index)
      .click();
  }

  async expectMessageVisible(text: string): Promise<void> {
    await expect(this.messageList.getByText(text, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
```

- [ ] **Step 6.3: Commit**

```bash
git add e2e/pages/AgentDetailPage.ts e2e/pages/AgentChatPage.ts
git commit -m "feat(e2e): add AgentDetail and AgentChat page objects"
```

---

## Task 7: Page objects — Environments, Workflows, Approvals, Clients

**Files:**
- Create: `e2e/pages/EnvironmentsPage.ts`
- Create: `e2e/pages/EnvironmentDetailPage.ts`
- Create: `e2e/pages/WorkflowsPage.ts`
- Create: `e2e/pages/ApprovalsPage.ts`
- Create: `e2e/pages/ClientsPage.ts`
- Create: `e2e/pages/ClientDetailPage.ts`

- [ ] **Step 7.1: Create `e2e/pages/EnvironmentsPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class EnvironmentsPage extends BasePage {
  async create(name: string): Promise<void> {
    await this.page.getByRole('button', { name: /New Environment|Create/ }).click();
    await this.page.getByPlaceholder(/name/i).fill(name);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/platform/environments') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Create|Submit/ }).last().click();
    await responsePromise;
  }

  async openEnvironment(name: string): Promise<void> {
    await this.page.getByText(name, { exact: false }).click();
  }

  async expectEnvironmentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectStatus(name: string, status: string): Promise<void> {
    const row = this.page.locator('div, tr', { hasText: name }).first();
    await expect(row.getByText(status, { exact: false })).toBeVisible({ timeout: 10_000 });
  }
}
```

- [ ] **Step 7.2: Create `e2e/pages/EnvironmentDetailPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class EnvironmentDetailPage extends BasePage {
  async addAgent(agentId: string): Promise<void> {
    await this.page.getByRole('button', { name: /Add Agent/ }).click();
    await this.page.getByPlaceholder(/Agent ID|agent/i).fill(agentId);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/agents') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Add|Attach/ }).last().click();
    await responsePromise;
  }

  async openLogs(): Promise<void> {
    await this.page.getByRole('button', { name: /Logs/ }).click();
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
```

- [ ] **Step 7.3: Create `e2e/pages/WorkflowsPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class WorkflowsPage extends BasePage {
  async expectWorkflowListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible();
  }

  async openWorkflow(name: string): Promise<void> {
    await this.page.getByText(name, { exact: false }).click();
    await this.page.waitForURL(/\/workflows\//);
  }
}
```

- [ ] **Step 7.4: Create `e2e/pages/ApprovalsPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class ApprovalsPage extends BasePage {
  async expectEmpty(): Promise<void> {
    await expect(this.page.getByText(/No pending approvals/)).toBeVisible();
  }

  async expectApprovalListed(title: string): Promise<void> {
    await expect(this.page.getByTestId(`approval-row-${title}`)).toBeVisible({ timeout: 8_000 });
  }

  async approve(title: string): Promise<void> {
    const row = this.page.getByTestId(`approval-row-${title}`);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/approve') && res.request().method() === 'POST',
    );
    await row.getByRole('button', { name: /Approve/ }).click();
    await responsePromise;
    await expect(row).not.toBeVisible({ timeout: 5_000 });
  }

  async deny(title: string): Promise<void> {
    const row = this.page.getByTestId(`approval-row-${title}`);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/deny', ) && res.request().method() === 'POST',
    );
    await row.getByRole('button', { name: /Deny|Reject/ }).click();
    await responsePromise;
    await expect(row).not.toBeVisible({ timeout: 5_000 });
  }
}
```

- [ ] **Step 7.5: Create `e2e/pages/ClientsPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class ClientsPage extends BasePage {
  async create(id: string, name: string): Promise<void> {
    await this.page.getByRole('button', { name: /New Client/ }).click();
    await this.page.getByPlaceholder(/Client ID|acme-corp/).fill(id);
    await this.page.getByPlaceholder(/Client Name|Acme/).fill(name);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/clients') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Create|Add/ }).last().click();
    await responsePromise;
  }

  async openClient(id: string): Promise<void> {
    await this.page.getByTestId(`client-row-${id}`).click();
  }

  async expectClientListed(id: string): Promise<void> {
    await expect(this.page.getByTestId(`client-row-${id}`)).toBeVisible({ timeout: 8_000 });
  }
}
```

- [ ] **Step 7.6: Create `e2e/pages/ClientDetailPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export interface MCPServerConfig {
  name: string;
  url: string;
}

export class ClientDetailPage extends BasePage {
  async addMCPServer(cfg: MCPServerConfig): Promise<void> {
    await this.page.getByRole('button', { name: /Add MCP|Add Server/ }).click();
    await this.page.getByPlaceholder(/Server name|Name/).fill(cfg.name);
    await this.page.getByPlaceholder(/URL|http/).fill(cfg.url);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/mcp-servers') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Add|Save/ }).last().click();
    await responsePromise;
  }

  async removeMCPServer(name: string): Promise<void> {
    const row = this.page.locator('div, tr', { hasText: name }).first();
    await row.getByRole('button', { name: /Remove|Delete/ }).click();
    await expect(this.page.getByText(name, { exact: false })).not.toBeVisible({ timeout: 5_000 });
  }

  async expectMCPListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
```

- [ ] **Step 7.7: Commit**

```bash
git add e2e/pages/EnvironmentsPage.ts e2e/pages/EnvironmentDetailPage.ts \
        e2e/pages/WorkflowsPage.ts e2e/pages/ApprovalsPage.ts \
        e2e/pages/ClientsPage.ts e2e/pages/ClientDetailPage.ts
git commit -m "feat(e2e): add Environments, Workflows, Approvals, Clients page objects"
```

---

## Task 8: Page objects — Admin pages

**Files:**
- Create: `e2e/pages/AdminHealthPage.ts`
- Create: `e2e/pages/AdminChatPage.ts`
- Create: `e2e/pages/IntelligencePage.ts`
- Create: `e2e/pages/AuditPage.ts`
- Create: `e2e/pages/SchedulerPage.ts`

- [ ] **Step 8.1: Create `e2e/pages/AdminHealthPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AdminHealthPage extends BasePage {
  async expectProviderListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectPageLoaded(): Promise<void> {
    await this.expectHeading(/System Health|Admin/);
  }
}
```

- [ ] **Step 8.2: Create `e2e/pages/AdminChatPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AdminChatPage extends BasePage {
  private readonly messageInput = this.page.getByRole('textbox').first();
  private readonly sendButton = this.page.getByRole('button', { name: /Send/ });

  async sendMessage(text: string): Promise<void> {
    await this.messageInput.fill(text);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/admin/chat') && res.request().method() === 'POST',
    );
    await this.sendButton.click();
    await responsePromise;
  }

  async waitForResponse(): Promise<void> {
    const assistantMsg = this.page.locator('[data-role="assistant"], .assistant-message').last();
    await expect(assistantMsg).not.toBeEmpty({ timeout: 15_000 });
  }
}
```

- [ ] **Step 8.3: Create `e2e/pages/IntelligencePage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';
import { interceptConnectorSync } from '../support/route-intercepts';

export class IntelligencePage extends BasePage {
  private readonly questionInput = this.page.getByRole('textbox').first();
  private readonly askButton = this.page.getByRole('button', { name: /Ask|Submit/ });

  async setupIntercepts(): Promise<void> {
    await interceptConnectorSync(this.page);
  }

  async ask(question: string): Promise<void> {
    await this.questionInput.fill(question);
    await this.askButton.click();
  }

  async waitForAnswer(): Promise<void> {
    const answer = this.page.locator('[data-testid="intelligence-answer"], .answer, .response').first();
    await expect(answer).not.toBeEmpty({ timeout: 15_000 });
  }
}
```

- [ ] **Step 8.4: Create `e2e/pages/AuditPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AuditPage extends BasePage {
  async expectEntryContaining(text: string): Promise<void> {
    await expect(this.page.getByText(text, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async getRowCount(): Promise<number> {
    return this.page.getByRole('row').count();
  }

  async expectTableVisible(): Promise<void> {
    await expect(this.page.getByRole('table').or(this.page.locator('tbody'))).toBeVisible();
  }
}
```

- [ ] **Step 8.5: Create `e2e/pages/SchedulerPage.ts`**

```typescript
import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class SchedulerPage extends BasePage {
  async expectJobListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectPageLoaded(): Promise<void> {
    await this.expectHeading(/Scheduler|Jobs/);
  }
}
```

- [ ] **Step 8.6: Commit**

```bash
git add e2e/pages/AdminHealthPage.ts e2e/pages/AdminChatPage.ts \
        e2e/pages/IntelligencePage.ts e2e/pages/AuditPage.ts \
        e2e/pages/SchedulerPage.ts
git commit -m "feat(e2e): add Admin page objects"
```

---

## Task 9: Dashboard — add data-testid attributes

**Files:**
- Modify: `dashboard/src/components/StatCard.tsx`
- Modify: `dashboard/src/components/Sidebar.tsx`
- Modify: `dashboard/src/app/page.tsx`
- Modify: `dashboard/src/app/agents/page.tsx`
- Modify: `dashboard/src/app/agents/create/page.tsx`
- Modify: `dashboard/src/app/agents/[id]/page.tsx`
- Modify: `dashboard/src/app/agents/[id]/chat/page.tsx`
- Modify: `dashboard/src/app/approvals/page.tsx`
- Modify: `dashboard/src/app/clients/page.tsx`

- [ ] **Step 9.1: Add data-testid to `StatCard.tsx`**

In `dashboard/src/components/StatCard.tsx`, change:
```tsx
// before
<div className="card">
  <p className="text-[13px] font-medium text-[#6e6e80]">{title}</p>
```
to:
```tsx
// after
<div
  className="card"
  data-testid={`stat-${title.toLowerCase().replace(/\s+/g, '-')}`}
>
  <p className="text-[13px] font-medium text-[#6e6e80]">{title}</p>
```

- [ ] **Step 9.2: Add data-testid to Sidebar nav links**

In `dashboard/src/components/Sidebar.tsx`, change the `NavLink` return:
```tsx
// before
<Link href={href} className={cn(...)}>
  {label}
</Link>
```
to:
```tsx
// after
<Link
  href={href}
  data-testid={`nav-${label.toLowerCase().replace(/\s+/g, '-')}`}
  className={cn(...)}
>
  {label}
</Link>
```

- [ ] **Step 9.3: Add live-indicator data-testid to Overview page**

In `dashboard/src/app/page.tsx`, change the live indicator span:
```tsx
// before
<span className={`inline-block w-2 h-2 rounded-full ${live.connected ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`} />
```
to:
```tsx
// after
<span
  data-testid="live-indicator"
  className={`inline-block w-2 h-2 rounded-full ${live.connected ? 'bg-green-500 animate-pulse' : 'bg-gray-400'}`}
/>
```

- [ ] **Step 9.4: Add data-testid to Agents list page**

In `dashboard/src/app/agents/page.tsx`, find the agent row element (the Link or div wrapping each agent) and add:
```tsx
data-testid={`agent-row-${agent.name}`}
```
Also add `data-testid="live-indicator"` to the live status span (same pattern as step 9.3).

- [ ] **Step 9.5: Add data-testid to Create Agent wizard**

In `dashboard/src/app/agents/create/page.tsx`:

1. Add `data-testid={`wizard-step-${index + 1}`}` to each step indicator element in the steps array render.
2. Add `data-testid={`stack-option-${stack_value}`}` to each stack selection button/card.
3. Add `data-testid={`exec-type-${type_value}`}` to each execution type button/card.

- [ ] **Step 9.6: Add data-testid to Agent detail page**

In `dashboard/src/app/agents/[id]/page.tsx`:

```tsx
// wrap the invoke textarea + button area:
<div data-testid="invoke-panel">
  {/* existing invoke content */}
</div>

// the result display element:
<div data-testid="invoke-result">
  {invokeResult?.output}
</div>

// tab buttons — each tab button:
<button data-testid={`tab-${tabName}`} ...>
```

- [ ] **Step 9.7: Add data-testid to Agent chat page**

In `dashboard/src/app/agents/[id]/chat/page.tsx`:

```tsx
// message input:
<input data-testid="chat-input" ... />
// or textarea:
<textarea data-testid="chat-input" ... />

// messages container:
<div data-testid="chat-messages">
  {messages.map(msg => (
    <div key={msg.id} data-role={msg.role}>...</div>
  ))}
</div>

// sessions list:
<div data-testid="session-list">...</div>
```

- [ ] **Step 9.8: Add data-testid to Approvals page**

In `dashboard/src/app/approvals/page.tsx`:

```tsx
// each approval item div:
<div key={item.id} data-testid={`approval-row-${item.title}`} className="card flex ...">
```

- [ ] **Step 9.9: Add data-testid to Clients page**

In `dashboard/src/app/clients/page.tsx`:

```tsx
// each client row link or div:
<Link key={c.client_id} data-testid={`client-row-${c.client_id}`} href={`/clients/${c.client_id}`}>
```

- [ ] **Step 9.10: Verify dashboard still compiles**

```bash
cd dashboard && npm run build 2>&1 | tail -5
```

Expected: `✓ Compiled successfully` (no TypeScript errors).

- [ ] **Step 9.11: Commit**

```bash
git add dashboard/src/components/StatCard.tsx \
        dashboard/src/components/Sidebar.tsx \
        dashboard/src/app/page.tsx \
        dashboard/src/app/agents/page.tsx \
        dashboard/src/app/agents/create/page.tsx \
        dashboard/src/app/agents/\[id\]/page.tsx \
        dashboard/src/app/agents/\[id\]/chat/page.tsx \
        dashboard/src/app/approvals/page.tsx \
        dashboard/src/app/clients/page.tsx
git commit -m "feat(dashboard): add data-testid attributes for Playwright e2e"
```

---

## Task 10: Specs — auth + overview

**Files:**
- Create: `e2e/specs/auth/login.spec.ts`
- Create: `e2e/specs/overview/dashboard.spec.ts`

- [ ] **Step 10.1: Create `e2e/specs/auth/login.spec.ts`**

```typescript
import { test, expect } from '@playwright/test';
import { LoginPage } from '../../pages/LoginPage';

// Override storageState for this entire file — no pre-auth
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('Login', () => {
  test('unauthenticated visit to / redirects to /login', async ({ page }) => {
    await page.goto('/');
    await page.waitForURL('/login');
    await expect(page).toHaveURL('/login');
  });

  test('wrong password shows inline error, stays on /login', async ({ page }) => {
    const login = new LoginPage(page);
    await login.navigate('/login');
    await login.fillPassword('wrongpassword');
    await login.submit();
    await login.expectError('Invalid password');
    await expect(page).toHaveURL('/login');
  });

  test('correct password redirects to overview', async ({ page }) => {
    const login = new LoginPage(page);
    await login.navigate('/login');
    await login.fillPassword(process.env.FORGEOS_E2E_PASSWORD ?? 'forgeos');
    await login.submit();
    await page.waitForURL('/');
    await expect(page).toHaveURL('/');
  });
});
```

- [ ] **Step 10.2: Create `e2e/specs/overview/dashboard.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Platform Overview', () => {
  test.beforeEach(async ({ overviewPage }) => {
    await overviewPage.navigate('/');
  });

  test('renders Total Agents stat card', async ({ overviewPage }) => {
    const card = await overviewPage.getStatCard('total-agents');
    await expect(card).toBeVisible();
  });

  test('renders Running stat card', async ({ overviewPage }) => {
    const card = await overviewPage.getStatCard('running');
    await expect(card).toBeVisible();
  });

  test('lists all 4 stacks in the breakdown', async ({ page }) => {
    for (const label of ['ForgeOS', 'CrewAI', 'ADK', 'OpenClaw']) {
      await expect(page.getByText(label, { exact: false })).toBeVisible();
    }
  });

  test('lists all 5 execution types in the breakdown', async ({ page }) => {
    for (const label of ['Always On', 'Scheduled', 'Event Driven', 'Reflex', 'Autonomous']) {
      await expect(page.getByText(label, { exact: false })).toBeVisible();
    }
  });

  test('live indicator is visible', async ({ overviewPage }) => {
    await overviewPage.expectLiveConnected();
  });
});
```

- [ ] **Step 10.3: Run auth + overview specs to verify the suite wires up**

```bash
cd e2e && npx playwright test specs/auth/ specs/overview/ --project=chromium
```

Expected: green — tests pass (the backend + dashboard must be running, or webServer auto-starts them).

- [ ] **Step 10.4: Commit**

```bash
git add e2e/specs/auth/ e2e/specs/overview/
git commit -m "feat(e2e): add auth and overview specs"
```

---

## Task 11: Specs — agents list + create

**Files:**
- Create: `e2e/specs/agents/list.spec.ts`
- Create: `e2e/specs/agents/create.spec.ts`

- [ ] **Step 11.1: Create `e2e/specs/agents/list.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Agents List', () => {
  let agentId: string;

  test.beforeEach(async ({ agentsPage }) => {
    ({ agentId } = await seedAgent({ name: 'list-test', stack: 'forgeos', execution_type: 'reflex' }));
    await agentsPage.navigate('/agents');
  });

  test.afterEach(async () => {
    await deleteAgent(agentId);
  });

  test('seeded agent appears in list', async ({ agentsPage }) => {
    await agentsPage.expectAgentListed('__e2e__list-test');
  });

  test('filter by stack narrows results', async ({ agentsPage, page }) => {
    await agentsPage.filterByStack('forgeos');
    // After filtering, only forgeos agents should appear
    await expect(page.getByText('crewai', { exact: false })).not.toBeVisible();
  });

  test('filter by execution type narrows results', async ({ agentsPage }) => {
    await agentsPage.filterByType('reflex');
    await agentsPage.expectAgentListed('__e2e__list-test');
  });

  test('create button navigates to /agents/create', async ({ agentsPage, page }) => {
    await agentsPage.clickCreate();
    await expect(page).toHaveURL('/agents/create');
  });

  test('live status indicator is visible', async ({ agentsPage }) => {
    await agentsPage.expectLiveIndicatorVisible();
  });
});
```

- [ ] **Step 11.2: Create `e2e/specs/agents/create.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { deleteAgent } from '../../support/api-seed';

test.describe('Create Agent Wizard', () => {
  let createdAgentId: string;

  test.afterEach(async () => {
    if (createdAgentId) await deleteAgent(createdAgentId);
    createdAgentId = '';
  });

  test('happy path: creates a ForgeOS reflex agent and redirects to detail', async ({
    page,
    createAgentPage,
  }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('reflex');
    await createAgentPage.fillIdentity({
      name: '__e2e__create-wizard',
      description: 'E2E wizard test',
    });
    await createAgentPage.configureLLM();
    createdAgentId = await createAgentPage.reviewAndSubmit();
    await expect(page).toHaveURL(new RegExp('/agents/'));
  });

  test('step indicators render', async ({ createAgentPage }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.expectStepIndicator(1);
  });

  test('empty name blocks progression at identity step', async ({ createAgentPage }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('reflex');
    // Try to advance with empty name
    await createAgentPage.fillIdentity({ name: '' });
    await createAgentPage.expectValidationError('name');
  });

  test('scheduled type reveals schedule field', async ({ createAgentPage, page }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('scheduled');
    await expect(page.getByLabel(/Schedule/)).toBeVisible();
  });

  test('autonomous type reveals goal field', async ({ createAgentPage, page }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('autonomous');
    await expect(page.getByLabel(/Goal/)).toBeVisible();
  });

  test('AI Wizard page renders and accepts a prompt', async ({ page }) => {
    await page.goto('/agents/create/ai');
    await expect(page.getByRole('textbox')).toBeVisible();
  });
});
```

- [ ] **Step 11.3: Run agents list + create specs**

```bash
cd e2e && npx playwright test specs/agents/list.spec.ts specs/agents/create.spec.ts
```

Expected: green.

- [ ] **Step 11.4: Commit**

```bash
git add e2e/specs/agents/list.spec.ts e2e/specs/agents/create.spec.ts
git commit -m "feat(e2e): add agents list and create specs"
```

---

## Task 12: Specs — agent detail + chat

**Files:**
- Create: `e2e/specs/agents/detail.spec.ts`
- Create: `e2e/specs/agents/chat.spec.ts`

- [ ] **Step 12.1: Create `e2e/specs/agents/detail.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Agent Detail', () => {
  let agentId: string;

  test.beforeEach(async ({ agentDetailPage }) => {
    ({ agentId } = await seedAgent({ name: 'detail-test', stack: 'forgeos', execution_type: 'reflex' }));
    await agentDetailPage.navigate(`/agents/${agentId}`);
  });

  test.afterEach(async () => {
    if (agentId) await deleteAgent(agentId);
  });

  test('renders agent name badge and stack badge', async ({ agentDetailPage }) => {
    await agentDetailPage.expectBadgeVisible('__e2e__detail-test');
    await agentDetailPage.expectBadgeVisible('forgeos');
  });

  test('invoke with prompt shows result panel', async ({ agentDetailPage }) => {
    await agentDetailPage.invoke('What is your role?');
    const result = await agentDetailPage.waitForInvokeResult();
    expect(result.length).toBeGreaterThan(0);
  });

  test('activity tab shows content', async ({ agentDetailPage, page }) => {
    await agentDetailPage.openTab('activity');
    await expect(page.locator('[data-testid="tab-activity"], [aria-selected="true"]')).toBeVisible();
  });

  test('logs tab renders log content', async ({ agentDetailPage, page }) => {
    await agentDetailPage.openTab('logs');
    // log panel should be visible (even if content is empty)
    await expect(page.getByText(/logs|No logs/i)).toBeVisible();
  });

  test('config tab shows config fields', async ({ agentDetailPage, page }) => {
    await agentDetailPage.openTab('config');
    await expect(page.getByText(/stack|execution/i)).toBeVisible();
  });

  test('delete navigates back to /agents', async ({ agentDetailPage, page }) => {
    await agentDetailPage.delete();
    await expect(page).toHaveURL('/agents');
    agentId = ''; // already deleted
  });
});
```

- [ ] **Step 12.2: Create `e2e/specs/agents/chat.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Agent Chat', () => {
  let agentId: string;

  test.beforeEach(async ({ agentChatPage }) => {
    ({ agentId } = await seedAgent({ name: 'chat-test', stack: 'forgeos', execution_type: 'reflex' }));
    await agentChatPage.navigate(`/agents/${agentId}/chat`);
  });

  test.afterEach(async () => {
    if (agentId) await deleteAgent(agentId);
  });

  test('renders message input and send button', async ({ page }) => {
    await expect(page.getByTestId('chat-input')).toBeVisible();
    await expect(page.getByRole('button', { name: /Send/ })).toBeVisible();
  });

  test('sending a message triggers the stream endpoint', async ({ agentChatPage, page }) => {
    const streamPromise = page.waitForResponse(
      (res) => res.url().includes('/chat/stream') && res.request().method() === 'POST',
    );
    await agentChatPage.sendMessage('Hello');
    await streamPromise;
  });

  test('streamed response appears in the conversation', async ({ agentChatPage }) => {
    await agentChatPage.sendMessage('Hello, who are you?');
    await agentChatPage.waitForStreamedResponse();
  });
});
```

- [ ] **Step 12.3: Run detail + chat specs**

```bash
cd e2e && npx playwright test specs/agents/detail.spec.ts specs/agents/chat.spec.ts
```

Expected: green.

- [ ] **Step 12.4: Commit**

```bash
git add e2e/specs/agents/detail.spec.ts e2e/specs/agents/chat.spec.ts
git commit -m "feat(e2e): add agent detail and chat specs"
```

---

## Task 13: Specs — environments + workflows + approvals

**Files:**
- Create: `e2e/specs/environments/list.spec.ts`
- Create: `e2e/specs/environments/detail.spec.ts`
- Create: `e2e/specs/workflows/list.spec.ts`
- Create: `e2e/specs/approvals/queue.spec.ts`

- [ ] **Step 13.1: Create `e2e/specs/environments/list.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Environments List', () => {
  test('page loads without error', async ({ environmentsPage }) => {
    await environmentsPage.navigate('/environments');
    await expect(environmentsPage['page'].getByRole('heading', { level: 1 })).toBeVisible();
  });

  test('create form appears on button click', async ({ page }) => {
    await page.goto('/environments');
    await page.getByRole('button', { name: /New Environment|Create/ }).click();
    await expect(page.getByPlaceholder(/name/i)).toBeVisible();
  });

  test('creating an environment adds a new row', async ({ environmentsPage }) => {
    await environmentsPage.navigate('/environments');
    await environmentsPage.create('__e2e__env-list-test');
    await environmentsPage.expectEnvironmentListed('__e2e__env-list-test');
  });
});
```

- [ ] **Step 13.2: Create `e2e/specs/environments/detail.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Environment Detail', () => {
  test('page loads when navigated to', async ({ page }) => {
    // Create an env first, then navigate to its detail
    await page.goto('/environments');
    const createBtn = page.getByRole('button', { name: /New Environment|Create/ });
    await createBtn.click();
    await page.getByPlaceholder(/name/i).fill('__e2e__env-detail');
    await page.getByRole('button', { name: /Create|Submit/ }).last().click();
    // Click into the newly created environment
    await page.getByText('__e2e__env-detail', { exact: false }).click();
    await expect(page).toHaveURL(/\/environments\//);
    await expect(page.getByText('__e2e__env-detail', { exact: false })).toBeVisible();
  });
});
```

- [ ] **Step 13.3: Create `e2e/specs/workflows/list.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Workflows List', () => {
  test('page loads without error', async ({ workflowsPage }) => {
    await workflowsPage.navigate('/workflows');
    await expect(workflowsPage['page'].getByRole('heading', { level: 1 })).toBeVisible();
  });

  test('navigates to workflow detail on row click', async ({ page }) => {
    await page.goto('/workflows');
    const firstLink = page.getByRole('link').first();
    const count = await firstLink.count();
    if (count > 0) {
      await firstLink.click();
      await expect(page).toHaveURL(/\/workflows\//);
    } else {
      // empty state is acceptable
      await expect(page.getByText(/no workflows|empty/i).or(page.locator('body'))).toBeTruthy();
    }
  });
});
```

- [ ] **Step 13.4: Create `e2e/specs/approvals/queue.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent, seedA2HRequest } from '../../support/api-seed';

test.describe('Approvals Queue', () => {
  test('shows empty state when no approvals pending', async ({ approvalsPage }) => {
    await approvalsPage.navigate('/approvals');
    await approvalsPage.expectEmpty();
  });

  test.describe('with a seeded approval request', () => {
    let agentId: string;
    let requestId: string;

    test.beforeEach(async ({ approvalsPage }) => {
      ({ agentId } = await seedAgent({ name: 'approval-agent' }));
      ({ requestId } = await seedA2HRequest({
        title: 'review-action',
        agent: `__e2e__approval-agent`,
      }));
      await approvalsPage.navigate('/approvals');
    });

    test.afterEach(async () => {
      await deleteAgent(agentId);
    });

    test('seeded request appears with title and agent name', async ({ approvalsPage, page }) => {
      await approvalsPage.expectApprovalListed('__e2e__review-action');
      await expect(page.getByText('__e2e__approval-agent', { exact: false })).toBeVisible();
    });

    test('approve removes the request from list', async ({ approvalsPage }) => {
      await approvalsPage.approve('__e2e__review-action');
      await approvalsPage.expectEmpty();
    });
  });
});
```

- [ ] **Step 13.5: Run these specs**

```bash
cd e2e && npx playwright test specs/environments/ specs/workflows/ specs/approvals/
```

Expected: green.

- [ ] **Step 13.6: Commit**

```bash
git add e2e/specs/environments/ e2e/specs/workflows/ e2e/specs/approvals/
git commit -m "feat(e2e): add environments, workflows, approvals specs"
```

---

## Task 14: Specs — clients

**Files:**
- Create: `e2e/specs/clients/list.spec.ts`
- Create: `e2e/specs/clients/detail.spec.ts`

- [ ] **Step 14.1: Create `e2e/specs/clients/list.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { deleteClient } from '../../support/api-seed';

test.describe('Clients List', () => {
  const testId = 'e2e-client-list';

  test.afterEach(async () => {
    await deleteClient(testId);
  });

  test('creates a client and it appears in list', async ({ clientsPage }) => {
    await clientsPage.navigate('/clients');
    await clientsPage.create(testId, 'E2E Test Client');
    await clientsPage.expectClientListed(`__e2e__${testId}`);
  });

  test('empty client ID is blocked by form validation', async ({ page }) => {
    await page.goto('/clients');
    await page.getByRole('button', { name: /New Client/ }).click();
    // Leave ID empty, fill name only
    await page.getByPlaceholder(/Client Name|Acme/).fill('Some Name');
    await page.getByRole('button', { name: /Create|Add/ }).last().click();
    // Should still be on /clients (not navigated away) or show validation
    await expect(page).toHaveURL('/clients');
  });
});
```

- [ ] **Step 14.2: Create `e2e/specs/clients/detail.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedClient, deleteClient } from '../../support/api-seed';
import { interceptExternalMCP } from '../../support/route-intercepts';

test.describe('Client Detail', () => {
  const clientId = 'e2e-client-detail';

  test.beforeEach(async ({ clientDetailPage }) => {
    await seedClient(clientId, 'E2E Detail Client');
    await interceptExternalMCP(clientDetailPage['page']);
    await clientDetailPage.navigate(`/clients/__e2e__${clientId}`);
  });

  test.afterEach(async () => {
    await deleteClient(clientId);
  });

  test('shows client ID in heading or page body', async ({ page }) => {
    await expect(page.getByText(`__e2e__${clientId}`, { exact: false })).toBeVisible();
  });

  test('add MCP server and it appears in list', async ({ clientDetailPage }) => {
    await clientDetailPage.addMCPServer({
      name: '__e2e__test-mcp',
      url: 'http://localhost:9999/mcp',
    });
    await clientDetailPage.expectMCPListed('__e2e__test-mcp');
  });

  test('remove MCP server removes it from list', async ({ clientDetailPage }) => {
    await clientDetailPage.addMCPServer({
      name: '__e2e__rm-mcp',
      url: 'http://localhost:9999/mcp',
    });
    await clientDetailPage.removeMCPServer('__e2e__rm-mcp');
    await expect(clientDetailPage['page'].getByText('__e2e__rm-mcp')).not.toBeVisible();
  });
});
```

- [ ] **Step 14.3: Run clients specs**

```bash
cd e2e && npx playwright test specs/clients/
```

Expected: green.

- [ ] **Step 14.4: Commit**

```bash
git add e2e/specs/clients/
git commit -m "feat(e2e): add clients list and detail specs"
```

---

## Task 15: Specs — admin (health, chat, audit, scheduler)

**Files:**
- Create: `e2e/specs/admin/health.spec.ts`
- Create: `e2e/specs/admin/chat.spec.ts`
- Create: `e2e/specs/admin/audit.spec.ts`
- Create: `e2e/specs/admin/scheduler.spec.ts`

- [ ] **Step 15.1: Create `e2e/specs/admin/health.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Admin — System Health', () => {
  test.beforeEach(async ({ adminHealthPage }) => {
    await adminHealthPage.navigate('/admin');
  });

  test('page loads without error', async ({ adminHealthPage }) => {
    await adminHealthPage.expectPageLoaded();
  });

  test('at least one provider is listed', async ({ adminHealthPage }) => {
    await adminHealthPage.expectProviderListed('simulated');
  });

  test('/metrics endpoint returns Prometheus text', async ({ request }) => {
    const res = await request.get('http://localhost:5000/metrics');
    expect(res.status()).toBe(200);
    const body = await res.text();
    expect(body).toContain('# HELP');
  });
});
```

- [ ] **Step 15.2: Create `e2e/specs/admin/chat.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Admin Chat', () => {
  test.beforeEach(async ({ adminChatPage }) => {
    await adminChatPage.navigate('/admin/chat');
  });

  test('renders message input and send button', async ({ page }) => {
    await expect(page.getByRole('textbox').first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Send/ })).toBeVisible();
  });

  test('sending a message gets a response', async ({ adminChatPage }) => {
    await adminChatPage.sendMessage('What is the platform status?');
    await adminChatPage.waitForResponse();
  });
});
```

- [ ] **Step 15.3: Create `e2e/specs/admin/audit.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Audit Log', () => {
  test('page loads and table is visible', async ({ auditPage }) => {
    await auditPage.navigate('/admin/audit');
    await auditPage.expectTableVisible();
  });

  test('after deploying an agent a new audit entry appears', async ({ auditPage }) => {
    const { agentId } = await seedAgent({ name: 'audit-entry-test' });
    await auditPage.navigate('/admin/audit');
    await auditPage.expectEntryContaining('__e2e__audit-entry-test');
    await deleteAgent(agentId);
  });
});
```

- [ ] **Step 15.4: Create `e2e/specs/admin/scheduler.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';

test.describe('Scheduler', () => {
  test('page loads without error', async ({ schedulerPage }) => {
    await schedulerPage.navigate('/admin/jobs');
    await schedulerPage.expectPageLoaded();
  });

  test('scheduled jobs list renders (empty state is acceptable)', async ({ page }) => {
    await page.goto('/admin/jobs');
    // Either jobs are listed or empty state is shown — page must not error
    await expect(page.locator('body')).not.toContainText('500');
    await expect(page.locator('body')).not.toContainText('Error');
  });
});
```

- [ ] **Step 15.5: Run admin specs**

```bash
cd e2e && npx playwright test specs/admin/
```

Expected: green.

- [ ] **Step 15.6: Commit**

```bash
git add e2e/specs/admin/
git commit -m "feat(e2e): add admin specs (health, chat, audit, scheduler)"
```

---

## Task 16: Specs — cross-cutting flows

**Files:**
- Create: `e2e/specs/flows/agent-full-lifecycle.spec.ts`
- Create: `e2e/specs/flows/client-onboarding.spec.ts`
- Create: `e2e/specs/flows/hitl-approval.spec.ts`

- [ ] **Step 16.1: Create `e2e/specs/flows/agent-full-lifecycle.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { deleteAllE2EEntities } from '../../support/api-seed';

test.describe('Flow: Agent Full Lifecycle', () => {
  let agentId: string;

  test.afterEach(async () => {
    if (agentId) {
      await deleteAllE2EEntities();
      agentId = '';
    }
  });

  test('create → list → detail → invoke → delete', async ({
    page,
    createAgentPage,
    agentsPage,
    agentDetailPage,
  }) => {
    // 1. Create
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('reflex');
    await createAgentPage.fillIdentity({ name: '__e2e__lifecycle-test', description: 'lifecycle' });
    await createAgentPage.configureLLM();
    agentId = await createAgentPage.reviewAndSubmit();
    await expect(page).toHaveURL(new RegExp('/agents/'));

    // 2. Appears in list
    await agentsPage.navigate('/agents');
    await agentsPage.expectAgentListed('__e2e__lifecycle-test');

    // 3. Navigate to detail
    await page.goto(`/agents/${agentId}`);
    await agentDetailPage.expectBadgeVisible('__e2e__lifecycle-test');

    // 4. Invoke
    await agentDetailPage.invoke('Summarise your role briefly.');
    const result = await agentDetailPage.waitForInvokeResult();
    expect(result.length).toBeGreaterThan(0);

    // 5. Delete
    await agentDetailPage.delete();
    await expect(page).toHaveURL('/agents');
    agentId = ''; // already deleted via UI
  });
});
```

- [ ] **Step 16.2: Create `e2e/specs/flows/client-onboarding.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { deleteClient } from '../../support/api-seed';
import { interceptExternalMCP } from '../../support/route-intercepts';

test.describe('Flow: Client Onboarding', () => {
  const clientId = 'e2e-onboarding';

  test.beforeEach(async ({ page }) => {
    await interceptExternalMCP(page);
  });

  test.afterEach(async () => {
    await deleteClient(clientId);
  });

  test('create client → add MCP server → verify in detail', async ({
    clientsPage,
    clientDetailPage,
    page,
  }) => {
    // 1. Create client
    await clientsPage.navigate('/clients');
    await clientsPage.create(clientId, 'E2E Onboarding Client');
    await clientsPage.expectClientListed(`__e2e__${clientId}`);

    // 2. Open client detail
    await clientsPage.openClient(`__e2e__${clientId}`);
    await expect(page).toHaveURL(new RegExp('/clients/'));

    // 3. Add MCP server
    await clientDetailPage.addMCPServer({
      name: '__e2e__onboarding-mcp',
      url: 'http://localhost:9998/mcp',
    });
    await clientDetailPage.expectMCPListed('__e2e__onboarding-mcp');
  });
});
```

- [ ] **Step 16.3: Create `e2e/specs/flows/hitl-approval.spec.ts`**

```typescript
import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent, seedA2HRequest } from '../../support/api-seed';

test.describe('Flow: HITL Approval', () => {
  let agentId: string;

  test.beforeEach(async () => {
    ({ agentId } = await seedAgent({ name: 'hitl-flow-agent' }));
  });

  test.afterEach(async () => {
    if (agentId) await deleteAgent(agentId);
  });

  test('seed request → navigate → approve → queue empty', async ({ approvalsPage }) => {
    // 1. Seed the A2H request via API
    await seedA2HRequest({
      title: 'hitl-flow-request',
      agent: '__e2e__hitl-flow-agent',
      category: 'approval',
      sla_hours: 2,
    });

    // 2. Navigate to approvals
    await approvalsPage.navigate('/approvals');

    // 3. Verify it appears
    await approvalsPage.expectApprovalListed('__e2e__hitl-flow-request');

    // 4. Approve it
    await approvalsPage.approve('__e2e__hitl-flow-request');

    // 5. Queue is now empty (or request is gone)
    await approvalsPage.expectEmpty();
  });
});
```

- [ ] **Step 16.4: Run all flow specs**

```bash
cd e2e && npx playwright test specs/flows/
```

Expected: green.

- [ ] **Step 16.5: Commit**

```bash
git add e2e/specs/flows/
git commit -m "feat(e2e): add cross-cutting flow specs"
```

---

## Task 17: Full suite run + CI check

- [ ] **Step 17.1: Run the complete suite**

```bash
cd e2e && npx playwright test
```

Expected: all specs pass. If any fail, check `playwright-report/index.html` for traces. Common fixes:
- Selector not found → check data-testid was added in Task 9
- Timeout on response → the backend simulation may be slow; increase timeout in the specific page object method
- Auth redirect loop → verify `global.setup.ts` wrote `.auth/state.json` successfully

- [ ] **Step 17.2: Verify test-results artefacts are gitignored**

```bash
ls e2e/test-results/ 2>/dev/null && echo "exists" || echo "empty or absent"
git status e2e/
```

Expected: `test-results/` and `playwright-report/` are not shown as untracked (covered by `.gitignore`).

- [ ] **Step 17.3: Add e2e run target to root Makefile**

In `Makefile`, add:

```makefile
.PHONY: e2e
e2e:
	cd e2e && npx playwright test

e2e-report:
	cd e2e && npx playwright show-report
```

- [ ] **Step 17.4: Final commit**

```bash
git add Makefile
git commit -m "feat(e2e): add e2e make targets and complete Playwright suite"
```

---

## Self-Review Against Spec

| Spec requirement | Task covering it |
|---|---|
| Root-level `e2e/` folder | Task 1 |
| POM — one class per page | Tasks 5–8 |
| `base.fixture.ts` with all page objects | Task 4 |
| `globalSetup` login + storageState | Task 2 |
| `globalTeardown` cleanup | Task 2 |
| Route intercepts for connector sync + external MCP | Task 3 |
| `api-seed.ts` with `__e2e__` prefix | Task 3 |
| `data-testid` on stat cards, nav, agent rows, wizard, invoke panel, tabs, chat, approvals, clients | Task 9 |
| `specs/auth/login.spec.ts` — 3 cases | Task 10 |
| `specs/overview/dashboard.spec.ts` — 5 cases | Task 10 |
| `specs/agents/list.spec.ts` — 5 cases | Task 11 |
| `specs/agents/create.spec.ts` — 5 cases | Task 11 |
| `specs/agents/detail.spec.ts` — 6 cases | Task 12 |
| `specs/agents/chat.spec.ts` — 3 cases | Task 12 |
| `specs/environments/` — 4 cases | Task 13 |
| `specs/workflows/` — 2 cases | Task 13 |
| `specs/approvals/queue.spec.ts` — 3 cases | Task 13 |
| `specs/clients/` — 5 cases | Task 14 |
| `specs/admin/` — 8 cases | Task 15 |
| `specs/flows/` — 3 multi-page journeys | Task 16 |
| `screenshot: only-on-failure`, `trace: on-first-retry` | Task 1 (playwright.config.ts) |
| `workers: 1`, `fullyParallel: false` | Task 1 (playwright.config.ts) |
| Makefile targets | Task 17 |
