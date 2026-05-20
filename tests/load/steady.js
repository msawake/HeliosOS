// -----------------------------------------------------------------------
// k6 steady-state test — sustained load at 100 RPS for 5 minutes.
//
// Simulates the minimum viable production load: ~100 concurrent users
// reading from the platform API, with a mix of read endpoints.
//
// Usage:
//   FORGEOS_BASE=https://staging.forgeos.example.com \
//     k6 run tests/load/steady.js
//
// Thresholds:
//   - p95 latency < 1s
//   - p99 latency < 3s
//   - error rate < 1%
// -----------------------------------------------------------------------
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '1m',  target: 20 },   // Ramp up
    { duration: '3m',  target: 100 },  // Sustain
    { duration: '1m',  target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_failed:   ['rate<0.01'],
    http_req_duration: ['p(95)<1000', 'p(99)<3000'],
    errors:            ['rate<0.01'],
  },
};

const BASE = __ENV.FORGEOS_BASE || 'http://localhost:5000';

export default function () {
  group('overview', function () {
    const r = http.get(`${BASE}/api/platform/overview`);
    errorRate.add(r.status !== 200);
    check(r, { overview_ok: (r) => r.status === 200 });
  });

  group('agents list', function () {
    const r = http.get(`${BASE}/api/platform/agents?limit=50`);
    errorRate.add(r.status !== 200);
    check(r, { agents_ok: (r) => r.status === 200 });
  });

  group('clients list', function () {
    const r = http.get(`${BASE}/api/clients`);
    errorRate.add(r.status !== 200);
    check(r, { clients_ok: (r) => r.status === 200 });
  });

  group('admin metrics', function () {
    const r = http.get(`${BASE}/api/admin/metrics`);
    errorRate.add(r.status !== 200);
    check(r, { metrics_ok: (r) => r.status === 200 });
  });

  group('skills search', function () {
    const r = http.get(`${BASE}/api/skills/search?query=sales`);
    errorRate.add(r.status !== 200);
    check(r, { skills_ok: (r) => r.status === 200 });
  });

  sleep(1);
}
