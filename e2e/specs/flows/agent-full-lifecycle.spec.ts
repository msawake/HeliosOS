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
    agentId = '';
  });
});
