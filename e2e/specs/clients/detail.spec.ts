import { test, expect } from '../../fixtures/base.fixture';
import { seedClient, deleteClient } from '../../support/api-seed';
import { interceptExternalMCP } from '../../support/route-intercepts';

test.describe('Client Detail', () => {
  const clientId = 'e2e-client-detail';

  test.beforeEach(async ({ clientDetailPage }) => {
    await seedClient(clientId, 'E2E Detail Client');
    await interceptExternalMCP(clientDetailPage['page']);
    await clientDetailPage.navigate(`/clients/__e2e__${clientId}`);
  });

  test.afterEach(async () => {
    await deleteClient(clientId);
  });

  test('shows client ID in heading or page body', async ({ page }) => {
    await expect(page.getByText(`__e2e__${clientId}`, { exact: false })).toBeVisible();
  });

  test('add MCP server and it appears in list', async ({ clientDetailPage }) => {
    await clientDetailPage.addMCPServer({
      name: '__e2e__test-mcp',
      url: 'http://localhost:9999/mcp',
    });
    await clientDetailPage.expectMCPListed('__e2e__test-mcp');
  });

  test('remove MCP server removes it from list', async ({ clientDetailPage }) => {
    await clientDetailPage.addMCPServer({
      name: '__e2e__rm-mcp',
      url: 'http://localhost:9999/mcp',
    });
    await clientDetailPage.removeMCPServer('__e2e__rm-mcp');
    await expect(clientDetailPage['page'].getByText('__e2e__rm-mcp')).not.toBeVisible();
  });
});
