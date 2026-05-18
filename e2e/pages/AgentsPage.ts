import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentsPage extends BasePage {
  private readonly stackFilter = this.page.locator('select').first();
  private readonly typeFilter = this.page.locator('select').nth(1);
  private readonly ownershipFilter = this.page.locator('select').nth(2);
  private readonly createLink = this.page.getByRole('link', { name: /Create Agent/ });

  async filterByStack(stack: string): Promise<void> {
    await this.stackFilter.selectOption(stack);
    await this.page.waitForLoadState('networkidle');
  }

  async filterByType(type: string): Promise<void> {
    await this.typeFilter.selectOption(type);
    await this.page.waitForLoadState('networkidle');
  }

  async filterByOwnership(ownership: string): Promise<void> {
    await this.ownershipFilter.selectOption(ownership);
    await this.page.waitForLoadState('networkidle');
  }

  async clickCreate(): Promise<void> {
    await this.createLink.click();
    await this.page.waitForURL('/agents/create');
  }

  async openAgent(name: string): Promise<void> {
    await this.page.getByTestId(`agent-row-${name}`).click();
  }

  async expectAgentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectLiveIndicatorVisible(): Promise<void> {
    await expect(this.page.getByTestId('live-indicator')).toBeVisible();
  }
}
