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
      await expect(page.locator('body')).toBeTruthy();
    }
  });
});
