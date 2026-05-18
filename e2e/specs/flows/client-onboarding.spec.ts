import { test, expect } from '../../fixtures/base.fixture';
import { deleteClient } from '../../support/api-seed';
import { interceptExternalMCP } from '../../support/route-intercepts';

test.describe('Flow: Client Onboarding', () => {
  const clientId = 'e2e-onboarding';

  test.beforeEach(async ({ page }) => {
    await interceptExternalMCP(page);
  });

  test.afterEach(async () => {
    await deleteClient(clientId);
  });

  test('create client → add MCP server → verify in detail', async ({
    clientsPage,
    clientDetailPage,
    page,
  }) => {
    // 1. Create client
    await clientsPage.navigate('/clients');
    await clientsPage.create(clientId, 'E2E Onboarding Client');
    await clientsPage.expectClientListed(`__e2e__${clientId}`);

    // 2. Open client detail
    await clientsPage.openClient(`__e2e__${clientId}`);
    await expect(page).toHaveURL(new RegExp('/clients/'));

    // 3. Add MCP server
    await clientDetailPage.addMCPServer({
      name: '__e2e__onboarding-mcp',
      url: 'http://localhost:9998/mcp',
    });
    await clientDetailPage.expectMCPListed('__e2e__onboarding-mcp');
  });
});
