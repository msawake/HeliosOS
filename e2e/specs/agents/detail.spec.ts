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
    await expect(page.getByText(/logs|No logs/i)).toBeVisible();
  });

  test('config tab shows config fields', async ({ agentDetailPage, page }) => {
    await agentDetailPage.openTab('config');
    await expect(page.getByText(/stack|execution/i)).toBeVisible();
  });

  test('delete navigates back to /agents', async ({ agentDetailPage, page }) => {
    await agentDetailPage.delete();
    await expect(page).toHaveURL('/agents');
    agentId = '';
  });
});
