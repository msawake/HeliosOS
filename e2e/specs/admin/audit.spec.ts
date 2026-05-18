import { test, expect } from '../../fixtures/base.fixture';
import { seedAgent, deleteAgent } from '../../support/api-seed';

test.describe('Audit Log', () => {
  test('page loads and table is visible', async ({ auditPage }) => {
    await auditPage.navigate('/admin/audit');
    await auditPage.expectTableVisible();
  });

  test('after deploying an agent a new audit entry appears', async ({ auditPage }) => {
    const { agentId } = await seedAgent({ name: 'audit-entry-test' });
    await auditPage.navigate('/admin/audit');
    await auditPage.expectEntryContaining('__e2e__audit-entry-test');
    await deleteAgent(agentId);
  });
});
