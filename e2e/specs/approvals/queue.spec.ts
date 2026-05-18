import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent, seedA2HRequest } from '../../support/api-seed';

test.describe('Approvals Queue', () => {
  test('shows empty state when no approvals pending', async ({ approvalsPage }) => {
    await approvalsPage.navigate('/approvals');
    await approvalsPage.expectEmpty();
  });

  test.describe('with a seeded approval request', () => {
    let agentId: string;

    test.beforeEach(async ({ approvalsPage }) => {
      ({ agentId } = await seedAgent({ name: 'approval-agent' }));
      await seedA2HRequest({
        title: 'review-action',
        agent: `__e2e__approval-agent`,
      });
      await approvalsPage.navigate('/approvals');
    });

    test.afterEach(async () => {
      await deleteAgent(agentId);
    });

    test('seeded request appears with title and agent name', async ({ approvalsPage, page }) => {
      await approvalsPage.expectApprovalListed('__e2e__review-action');
      await expect(page.getByText('__e2e__approval-agent', { exact: false })).toBeVisible();
    });

    test('approve removes the request from list', async ({ approvalsPage }) => {
      await approvalsPage.approve('__e2e__review-action');
      await approvalsPage.expectEmpty();
    });
  });
});
