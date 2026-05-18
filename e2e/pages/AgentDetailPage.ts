import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentDetailPage extends BasePage {
  private readonly invokePanel = this.page.getByTestId('invoke-panel');
  private readonly invokeResult = this.page.getByTestId('invoke-result');
  private readonly stopButton = this.page.getByRole('button', { name: /Stop/ });
  private readonly deleteButton = this.page.getByRole('button', { name: /Delete|Undeploy/ });

  async invoke(prompt: string): Promise<void> {
    await this.invokePanel.getByRole('textbox').fill(prompt);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/invoke') && res.request().method() === 'POST',
    );
    await this.invokePanel.getByRole('button', { name: /Invoke|Run/ }).click();
    await responsePromise;
  }

  async waitForInvokeResult(): Promise<string> {
    await expect(this.invokeResult).toBeVisible({ timeout: 15_000 });
    return (await this.invokeResult.textContent()) ?? '';
  }

  async openTab(name: 'activity' | 'logs' | 'config'): Promise<void> {
    await this.page.getByTestId(`tab-${name}`).click();
    await this.page.waitForLoadState('networkidle');
  }

  async stop(): Promise<void> {
    await this.stopButton.click();
    await this.waitForResponse('POST', '/stop');
  }

  async delete(): Promise<void> {
    await this.deleteButton.click();
    const confirmButton = this.page.getByRole('button', { name: /Confirm|Yes|Delete/ }).last();
    await confirmButton.click();
    await this.page.waitForURL('/agents');
  }

  async expectBadgeVisible(text: string): Promise<void> {
    await expect(this.page.getByText(text, { exact: false })).toBeVisible();
  }
}
