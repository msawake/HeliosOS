import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Agent Chat', () => {
  let agentId: string;

  test.beforeEach(async ({ agentChatPage }) => {
    ({ agentId } = await seedAgent({ name: 'chat-test', stack: 'forgeos', execution_type: 'reflex' }));
    await agentChatPage.navigate(`/agents/${agentId}/chat`);
  });

  test.afterEach(async () => {
    if (agentId) await deleteAgent(agentId);
  });

  test('renders message input and send button', async ({ page }) => {
    await expect(page.getByTestId('chat-input')).toBeVisible();
    await expect(page.getByRole('button', { name: /Send/ })).toBeVisible();
  });

  test('sending a message triggers the stream endpoint', async ({ agentChatPage, page }) => {
    const streamPromise = page.waitForResponse(
      (res) => res.url().includes('/chat') && res.request().method() === 'POST',
    );
    await agentChatPage.sendMessage('Hello');
    await streamPromise;
  });

  test('streamed response appears in the conversation', async ({ agentChatPage }) => {
    await agentChatPage.sendMessage('Hello, who are you?');
    await agentChatPage.waitForStreamedResponse();
  });
});
