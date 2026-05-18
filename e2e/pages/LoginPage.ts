import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export class LoginPage extends BasePage {
  private readonly passwordInput = this.page.getByPlaceholder('Enter password');
  private readonly submitButton = this.page.getByRole('button', { name: 'Sign in' });

  async fillPassword(password: string): Promise<void> {
    await this.passwordInput.fill(password);
  }

  async submit(): Promise<void> {
    await this.submitButton.click();
  }

  async expectError(message: string): Promise<void> {
    await expect(this.page.getByText(message)).toBeVisible({ timeout: 5_000 });
  }
}
