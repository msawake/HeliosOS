import { test, expect } from '../../fixtures/base.fixture';
import { deleteClient } from '../../support/api-seed';

test.describe('Clients List', () => {
  const testId = 'e2e-client-list';

  test.afterEach(async () => {
    await deleteClient(testId);
  });

  test('creates a client and it appears in list', async ({ clientsPage }) => {
    await clientsPage.navigate('/clients');
    await clientsPage.create(testId, 'E2E Test Client');
    await clientsPage.expectClientListed(`__e2e__${testId}`);
  });

  test('empty client ID is blocked by form validation', async ({ page }) => {
    await page.goto('/clients');
    await page.getByRole('button', { name: /New Client/ }).click();
    await page.getByPlaceholder(/Client Name|Acme/).fill('Some Name');
    await page.getByRole('button', { name: /Create|Add/ }).last().click();
    await expect(page).toHaveURL('/clients');
  });
});
