import { test as setup, expect } from '@playwright/test';
import { AUTH_FILE } from './playwright.config';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'ForgeOS' })).toBeVisible();
  await page.getByPlaceholder('Enter password').fill(
    process.env.FORGEOS_E2E_PASSWORD ?? 'forgeos',
  );
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('/');
  await expect(page.getByRole('main')).toBeVisible();
  await page.context().storageState({ path: AUTH_FILE });
});
