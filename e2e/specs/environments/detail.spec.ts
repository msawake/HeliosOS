import { test, expect } from '../../fixtures/base.fixture';

test.describe('Environment Detail', () => {
  test('page loads when navigated to', async ({ page }) => {
    await page.goto('/environments');
    const createBtn = page.getByRole('button', { name: /New Environment|Create/ });
    await createBtn.click();
    await page.getByPlaceholder(/name/i).fill('__e2e__env-detail');
    await page.getByRole('button', { name: /Create|Submit/ }).last().click();
    await page.getByText('__e2e__env-detail', { exact: false }).click();
    await expect(page).toHaveURL(/\/environments\//);
    await expect(page.getByText('__e2e__env-detail', { exact: false })).toBeVisible();
  });
});
