import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AuditPage extends BasePage {
  async expectEntryContaining(text: string): Promise<void> {
    await expect(this.page.getByText(text, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async getRowCount(): Promise<number> {
    return this.page.getByRole('row').count();
  }

  async expectTableVisible(): Promise<void> {
    await expect(this.page.getByRole('table').or(this.page.locator('tbody'))).toBeVisible();
  }
}
