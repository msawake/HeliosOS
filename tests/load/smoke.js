// -----------------------------------------------------------------------
// k6 smoke test — minimal sanity check that the API is alive and healthy.
//
// Runs a single virtual user for 30 seconds, hitting the public endpoints:
//   - /api/health
//   - /api/readiness
//   - /api/platform/overview
//   - /api/clients
//   - /api/admin/providers
//
// Usage:
//   k6 run tests/load/smoke.js
//   FORGEOS_BASE=https://staging.forgeos.example.com k6 run tests/load/smoke.js
//
// Thresholds cause exit code 1 if any endpoint is slow or errors.
// -----------------------------------------------------------------------
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds: {
    http_req_failed:   ['rate<0.01'],       // < 1% failures
    http_req_duration: ['p(95)<500'],       // p95 under 500ms
  },
};

const BASE = __ENV.FORGEOS_BASE || 'http://localhost:5000';

export default function () {
  const endpoints = [
    '/api/health',
    '/api/readiness',
    '/api/platform/overview',
    '/api/clients',
    '/api/admin/providers',
    '/api/admin/metrics',
    '/api/platform/scheduler',
  ];

  for (const path of endpoints) {
    const res = http.get(`${BASE}${path}`);
    check(res, {
      [`${path} 200`]: (r) => r.status === 200,
      [`${path} fast`]: (r) => r.timings.duration < 500,
    });
  }
  sleep(1);
}
