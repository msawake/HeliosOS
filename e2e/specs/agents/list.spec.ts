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
