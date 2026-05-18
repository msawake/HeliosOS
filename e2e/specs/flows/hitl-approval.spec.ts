import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent, seedA2HRequest } from '../../support/api-seed';

test.describe('Flow: HITL Approval', () => {
  let agentId: string;

  test.beforeEach(async () => {
    ({ agentId } = await seedAgent({ name: 'hitl-flow-agent' }));
  });

  test.afterEach(async () => {
    if (agentId) await deleteAgent(agentId);
  });

  test('seed request → navigate → approve → queue empty', async ({ approvalsPage }) => {
    await seedA2HRequest({
      title: 'hitl-flow-request',
      agent: '__e2e__hitl-flow-agent',
      category: 'approval',
      sla_hours: 2,
    });

    await approvalsPage.navigate('/approvals');
    await approvalsPage.expectApprovalListed('__e2e__hitl-flow-request');
    await approvalsPage.approve('__e2e__hitl-flow-request');
    await approvalsPage.expectEmpty();
  });
});
