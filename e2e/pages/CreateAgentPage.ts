import { expect } from '@playwright/test';
import { BasePage } from './BasePage';

export interface IdentityOpts {
  name: string;
  description?: string;
  department?: string;
  goal?: string;
  schedule?: string;
}

export interface LLMOpts {
  provider?: string;
  model?: string;
}

export class CreateAgentPage extends BasePage {
  private readonly nextButton = this.page.getByRole('button', { name: /Next/ });
  private readonly deployButton = this.page.getByRole('button', { name: /Deploy Agent/ });

  async selectStack(stack: string): Promise<void> {
    // Card click auto-advances to step 1 — no Next button needed
    await this.page.getByTestId(`stack-option-${stack}`).click();
  }

  async selectExecutionType(type: string): Promise<void> {
    // Card click auto-advances to step 2 — no Next button needed
    await this.page.getByTestId(`exec-type-${type}`).click();
  }

  async fillIdentity(opts: IdentityOpts): Promise<void> {
    await this.page.getByPlaceholder('e.g. inbox-manager').fill(opts.name);
    if (opts.description) await this.page.getByPlaceholder('What does this agent do?').fill(opts.description);
    if (opts.department) await this.page.getByPlaceholder('e.g. marketing').fill(opts.department);
    if (opts.goal) await this.page.getByPlaceholder('What should this agent achieve?').fill(opts.goal);
    if (opts.schedule) await this.page.getByPlaceholder('e.g. every 15m, */30 * * * *').fill(opts.schedule);
    await this.nextButton.click();
  }

  async configureLLM(opts: LLMOpts = {}): Promise<void> {
    if (opts.provider) {
      await this.page.locator('select').first().selectOption(opts.provider);
    }
    await this.nextButton.click();
  }

  async reviewAndSubmit(): Promise<string> {
    const responsePromise = this.page.waitForResponse(
      (res) => res.url().includes('/api/platform/agents') && res.request().method() === 'POST',
    );
    await this.deployButton.click();
    const res = await responsePromise;
    const data = (await res.json()) as { agent_id?: string };
    await this.page.waitForURL(/\/agents\//);
    return data.agent_id ?? '';
  }

  async expectValidationError(fieldPattern: string | RegExp): Promise<void> {
    await expect(
      this.page.getByText(typeof fieldPattern === 'string' ? new RegExp(fieldPattern, 'i') : fieldPattern),
    ).toBeVisible({ timeout: 5_000 });
  }

  async expectStepIndicator(stepNumber: number): Promise<void> {
    await expect(this.page.getByTestId(`wizard-step-${stepNumber}`)).toBeVisible();
  }
}
