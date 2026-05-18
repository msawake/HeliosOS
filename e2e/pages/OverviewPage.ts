import { expect, type Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class OverviewPage extends BasePage {
  async getStatCard(slug: string): Promise<Locator> {
    return this.page.getByTestId(`stat-${slug}`);
  }

  async expectLiveConnected(): Promise<void> {
    await expect(this.page.getByTestId('live-indicator')).toHaveClass(/bg-green-500/, {
      timeout: 8_000,
    });
  }

  async expectStackListed(label: string): Promise<void> {
    await expect(this.page.getByText(label, { exact: false })).toBeVisible();
  }

  async expectExecTypeListed(label: string): Promise<void> {
    await expect(this.page.getByText(label, { exact: false })).toBeVisible();
  }
}
