import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class EnvironmentDetailPage extends BasePage {
  async addAgent(agentId: string): Promise<void> {
    await this.page.getByRole('button', { name: /Add Agent/ }).click();
    await this.page.getByPlaceholder(/Agent ID|agent/i).fill(agentId);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/agents') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Add|Attach/ }).last().click();
    await responsePromise;
  }

  async openLogs(): Promise<void> {
    await this.page.getByRole('button', { name: /Logs/ }).click();
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
