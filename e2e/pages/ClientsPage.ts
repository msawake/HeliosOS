import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class ClientsPage extends BasePage {
  async create(id: string, name: string): Promise<void> {
    await this.page.getByRole('button', { name: /New Client/ }).click();
    await this.page.getByPlaceholder(/Client ID|acme-corp/).fill(id);
    await this.page.getByPlaceholder(/Client Name|Acme/).fill(name);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/clients') && res.request().method() === 'POST',
    );
    await this.page.getByRole('button', { name: /Create|Add/ }).last().click();
    await responsePromise;
  }

  async openClient(id: string): Promise<void> {
    await this.page.getByTestId(`client-row-${id}`).click();
  }

  async expectClientListed(id: string): Promise<void> {
    await expect(this.page.getByTestId(`client-row-${id}`)).toBeVisible({ timeout: 8_000 });
  }
}
