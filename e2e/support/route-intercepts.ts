import type { Page } from '@playwright/test';

export async function interceptConnectorSync(page: Page): Promise<void> {
  await page.route('**/api/intelligence/connectors/sync', async (route) => {
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'queued' }),
    });
  });
}

export async function interceptExternalMCP(page: Page): Promise<void> {
  await page.route(/^(?!http:\/\/localhost).*\/(mcp|tools)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tools: [] }),
    });
  });
}
