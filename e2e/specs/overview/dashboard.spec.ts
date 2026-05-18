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
