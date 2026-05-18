import { test as setup, expect } from '@playwright/test';
import path from 'path';
import fs from 'fs';

const AUTH_FILE = path.join(__dirname, '.auth/state.json');

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: 'ForgeOS' })).toBeVisible();
  await page.getByPlaceholder('Enter password').fill(
    process.env.FORGEOS_E2E_PASSWORD ?? 'forgeos',
  );
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('/');
  fs.mkdirSync(path.dirname(AUTH_FILE), { recursive: true });
  await page.context().storageState({ path: AUTH_FILE });
});
