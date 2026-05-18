import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AgentChatPage extends BasePage {
  private readonly messageInput = this.page.getByTestId('chat-input');
  private readonly sendButton = this.page.getByRole('button', { name: /Send/ });
  private readonly messageList = this.page.getByTestId('chat-messages');

  async sendMessage(text: string): Promise<void> {
    await this.messageInput.fill(text);
    await this.sendButton.click();
  }

  async waitForStreamedResponse(): Promise<void> {
    await expect(
      this.messageList.locator('[data-role="assistant"]').last(),
    ).not.toBeEmpty({ timeout: 15_000 });
  }

  async newSession(): Promise<void> {
    await this.page.getByRole('button', { name: /New [Ss]ession/ }).click();
  }

  async deleteSession(index = 0): Promise<void> {
    await this.page
      .getByTestId('session-list')
      .getByRole('button', { name: /Delete/ })
      .nth(index)
      .click();
  }

  async expectMessageVisible(text: string): Promise<void> {
    await expect(this.messageList.getByText(text, { exact: false })).toBeVisible({ timeout: 8_000 });
  }
}
