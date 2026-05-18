import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class SchedulerPage extends BasePage {
  async expectJobListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectPageLoaded(): Promise<void> {
    await this.expectHeading(/Scheduler|Jobs/);
  }
}
