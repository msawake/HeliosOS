import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class AdminChatPage extends BasePage {
  private readonly messageInput = this.page.getByRole('textbox').first();
  private readonly sendButton = this.page.getByRole('button', { name: /Send/ });

  async sendMessage(text: string): Promise<void> {
    await this.messageInput.fill(text);
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/admin/chat') && res.request().method() === 'POST',
    );
    await this.sendButton.click();
    await responsePromise;
  }

  async waitForAssistantMessage(): Promise<void> {
    const assistantMsg = this.page.locator('[data-role="assistant"], .assistant-message').last();
    await expect(assistantMsg).not.toBeEmpty({ timeout: 15_000 });
  }
}
