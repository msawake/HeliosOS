import { type Page, expect } from '@playwright/test';

export class BasePage {
  constructor(protected readonly page: Page) {}

  async navigate(path: string): Promise<void> {
    await this.page.goto(path);
    await this.page.waitForLoadState('networkidle');
  }

  async waitForInlineError(text?: string): Promise<void> {
    const el = this.page.locator('[class*="red"], [class*="error"]').first();
    await el.waitFor({ state: 'visible', timeout: 8_000 });
    if (text) await expect(el).toContainText(text);
  }

  async waitForResponse(method: string, urlPattern: string | RegExp): Promise<void> {
    await this.page.waitForResponse(
      (res) =>
        res.request().method().toUpperCase() === method.toUpperCase() &&
        (typeof urlPattern === 'string'
          ? res.url().includes(urlPattern)
          : urlPattern.test(res.url())),
      { timeout: 15_000 },
    );
  }

  async expectHeading(text: string | RegExp): Promise<void> {
    await expect(this.page.getByRole('heading', { level: 1 })).toContainText(text);
  }
}
