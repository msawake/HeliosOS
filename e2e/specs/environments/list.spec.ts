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
