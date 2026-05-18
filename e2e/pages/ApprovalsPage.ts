import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class ApprovalsPage extends BasePage {
  async expectEmpty(): Promise<void> {
    await expect(this.page.getByText(/No pending approvals/)).toBeVisible();
  }

  async expectApprovalListed(title: string): Promise<void> {
    await expect(this.page.getByTestId(`approval-row-${title}`)).toBeVisible({ timeout: 8_000 });
  }

  async approve(title: string): Promise<void> {
    const row = this.page.getByTestId(`approval-row-${title}`);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/approve') && res.request().method() === 'POST',
    );
    await row.getByRole('button', { name: /Approve/ }).click();
    await responsePromise;
    await expect(row).not.toBeVisible({ timeout: 5_000 });
  }

  async deny(title: string): Promise<void> {
    const row = this.page.getByTestId(`approval-row-${title}`);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/deny') && res.request().method() === 'POST',
    );
    await row.getByRole('button', { name: /Deny|Reject/ }).click();
    await responsePromise;
    await expect(row).not.toBeVisible({ timeout: 5_000 });
  }
}
