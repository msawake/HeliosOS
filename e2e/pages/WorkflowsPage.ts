import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class WorkflowsPage extends BasePage {
  async expectWorkflowListed(name: string): Promise<void> {
    await expect(this.page.getByText(name, { exact: false })).toBeVisible();
  }

  async openWorkflow(name: string): Promise<void> {
    await this.page.getByText(name, { exact: false }).click();
    await this.page.waitForURL(/\/workflows\//);
  }
}
