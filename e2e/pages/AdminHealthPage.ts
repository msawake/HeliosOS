import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AdminHealthPage extends BasePage {
  async expectProviderListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectPageLoaded(): Promise<void> {
    await this.expectHeading(/System Health|Admin/);
  }
}
