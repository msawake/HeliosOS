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
