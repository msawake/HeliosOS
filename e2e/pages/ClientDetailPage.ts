import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export interface MCPServerConfig {
  name: string;
  url: string;
}

export class ClientDetailPage extends BasePage {
  async addMCPServer(cfg: MCPServerConfig): Promise<void> {
    await this.page.getByRole('button', { name: /Add MCP|Add Server/ }).click();
    await this.page.getByPlaceholder(/Server name|Name/).fill(cfg.name);
    await this.page.getByPlaceholder(/URL|http/).fill(cfg.url);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/mcp-servers') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Add|Save/ }).last().click();
    await responsePromise;
  }

  async removeMCPServer(name: string): Promise<void> {
    const row = this.page.locator('div, tr', { hasText: name }).first();
    await row.getByRole('button', { name: /Remove|Delete/ }).click();
    await expect(this.page.getByText(name, { exact: false })).not.toBeVisible({ timeout: 5_000 });
  }

  async expectMCPListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
