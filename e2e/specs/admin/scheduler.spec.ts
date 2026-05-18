import { test, expect } from '../../fixtures/base.fixture';

test.describe('Scheduler', () => {
  test('page loads without error', async ({ schedulerPage }) => {
    await schedulerPage.navigate('/admin/jobs');
    await schedulerPage.expectPageLoaded();
  });

  test('scheduled jobs list renders (empty state is acceptable)', async ({ page }) => {
    await page.goto('/admin/jobs');
    await expect(page.locator('body')).not.toContainText('500');
    await expect(page.locator('body')).not.toContainText('Error');
  });
});
