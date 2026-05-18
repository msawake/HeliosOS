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

  test('empty name blocks deployment at review step', async ({ createAgentPage, page }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('reflex');
    await createAgentPage.fillIdentity({ name: '' });
    // Deploy button should be disabled when name is empty — stay on page
    await expect(page.getByRole('button', { name: /Deploy Agent/ })).toBeDisabled();
  });

  test('scheduled type reveals schedule field', async ({ createAgentPage, page }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('scheduled');
    await expect(page.getByPlaceholder(/every 15m/)).toBeVisible();
  });

  test('autonomous type reveals goal field', async ({ createAgentPage, page }) => {
    await createAgentPage.navigate('/agents/create');
    await createAgentPage.selectStack('forgeos');
    await createAgentPage.selectExecutionType('autonomous');
    await expect(page.getByPlaceholder(/What should this agent achieve/)).toBeVisible();
  });

  test('AI Wizard page renders and accepts a prompt', async ({ page }) => {
    await page.goto('/agents/create/ai');
    await expect(page.getByRole('textbox')).toBeVisible();
  });
});
