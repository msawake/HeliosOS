import { test, expect } from '../../fixtures/base.fixture';

test.describe('Admin Chat', () => {
  test.beforeEach(async ({ adminChatPage }) => {
    await adminChatPage.navigate('/admin/chat');
  });

  test('renders message input and send button', async ({ page }) => {
    await expect(page.getByRole('textbox').first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Send/ })).toBeVisible();
  });

  test('sending a message gets a response', async ({ adminChatPage }) => {
    await adminChatPage.sendMessage('What is the platform status?');
    await adminChatPage.waitForAssistantMessage();
  });
});
