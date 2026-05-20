// -----------------------------------------------------------------------
// k6 spike test — traffic spike from 10 → 500 VUs and back.
//
// Validates that the system can absorb a sudden traffic burst without
// falling over. Expected behavior:
//   - HPA scales up quickly
//   - Error rate stays below 5% during the spike
//   - P99 latency degrades but recovers within 2 minutes post-spike
//
// Usage:
//   FORGEOS_BASE=https://staging.forgeos.example.com \
//     k6 run tests/load/spike.js
// -----------------------------------------------------------------------
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '1m',  target: 10 },    // Baseline
    { duration: '30s', target: 500 },   // Sudden spike
    { duration: '2m',  target: 500 },   // Sustain at peak
    { duration: '1m',  target: 10 },    // Drain
    { duration: '1m',  target: 0 },     // Cool down
  ],
  thresholds: {
    http_req_failed:   ['rate<0.05'],   // < 5% failures during spike
    http_req_duration: ['p(95)<3000'],  // p95 < 3s even during spike
  },
};

const BASE = __ENV.FORGEOS_BASE || 'http://localhost:5000';

export default function () {
  const r = http.get(`${BASE}/api/platform/agents`);
  check(r, {
    status_200: (r) => r.status === 200,
    not_throttled: (r) => r.status !== 429,
  });
  sleep(0.5);
}
