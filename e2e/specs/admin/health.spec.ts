import { test, expect } from '../../fixtures/base.fixture';

test.describe('Admin — System Health', () => {
  test.beforeEach(async ({ adminHealthPage }) => {
    await adminHealthPage.navigate('/admin');
  });

  test('page loads without error', async ({ adminHealthPage }) => {
    await adminHealthPage.expectPageLoaded();
  });

  test('at least one provider is listed', async ({ adminHealthPage }) => {
    await adminHealthPage.expectProviderListed('simulated');
  });

  test('/metrics endpoint returns Prometheus text', async ({ request }) => {
    const res = await request.get('http://localhost:5000/metrics');
    expect(res.status()).toBe(200);
    const body = await res.text();
    expect(body).toContain('# HELP');
  });
});
