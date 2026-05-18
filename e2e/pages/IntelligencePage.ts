import { expect } from '@playwright/test';
import { BasePage } from './BasePage';
import { interceptConnectorSync } from '../support/route-intercepts';

export class IntelligencePage extends BasePage {
  private readonly questionInput = this.page.getByRole('textbox').first();
  private readonly askButton = this.page.getByRole('button', { name: /Ask|Submit/ });

  async setupIntercepts(): Promise<void> {
    await interceptConnectorSync(this.page);
  }

  async ask(question: string): Promise<void> {
    await this.questionInput.fill(question);
    await this.askButton.click();
  }

  async waitForAnswer(): Promise<void> {
    const answer = this.page.locator('[data-testid="intelligence-answer"], .answer, .response').first();
    await expect(answer).not.toBeEmpty({ timeout: 15_000 });
  }
}
