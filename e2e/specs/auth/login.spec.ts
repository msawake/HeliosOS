import { test, expect } from '@playwright/test';
import { LoginPage } from '../../pages/LoginPage';

// Override storageState for this file — no pre-auth
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('Login', () => {
  test('unauthenticated visit to / redirects to /login', async ({ page }) => {
    await page.goto('/');
    await page.waitForURL('/login');
    await expect(page).toHaveURL('/login');
  });

  test('wrong password shows inline error, stays on /login', async ({ page }) => {
    const login = new LoginPage(page);
    await login.navigate('/login');
    await login.fillPassword('wrongpassword');
    await login.submit();
    await login.expectError('Invalid password');
    await expect(page).toHaveURL('/login');
  });

  test('correct password redirects to overview', async ({ page }) => {
    const login = new LoginPage(page);
    await login.navigate('/login');
    await login.fillPassword(process.env.FORGEOS_E2E_PASSWORD ?? 'forgeos');
    await login.submit();
    await page.waitForURL('/');
    await expect(page).toHaveURL('/');
  });
});
