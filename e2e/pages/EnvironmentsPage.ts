import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class EnvironmentsPage extends BasePage {
  async create(name: string): Promise<void> {
    await this.page.getByRole('button', { name: /New Environment|Create/ }).click();
    await this.page.getByPlaceholder(/name/i).fill(name);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/platform/environments') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Create|Submit/ }).last().click();
    await responsePromise;
  }

  async openEnvironment(name: string): Promise<void> {
    await this.page.getByText(name, { exact: false }).click();
  }

  async expectEnvironmentListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible({ timeout: 8_000 });
  }

  async expectStatus(name: string, status: string): Promise<void> {
    const row = this.page.locator('div, tr', { hasText: name }).first();
    await expect(row.getByText(status, { exact: false })).toBeVisible({ timeout: 10_000 });
  }
}
