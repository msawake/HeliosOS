// -----------------------------------------------------------------------
// k6 agent invocation load test.
//
// Exercises the actual LLM path (/api/platform/agents/{id}/invoke). Much
// slower and more expensive than the read-only steady test — this hits
// real Anthropic/OpenAI APIs and consumes tokens.
//
// Prerequisites:
//   - At least one REFLEX agent deployed (agent_id in TARGET_AGENT_ID)
//   - ANTHROPIC_API_KEY set in the backend
//   - Ideally running against a staging env with generous token budget
//
// Usage:
//   TARGET_AGENT_ID=abc123 FORGEOS_BASE=https://staging.forgeos.example.com \
//     FORGEOS_API_KEY=... k6 run tests/load/invoke-agent.js
// -----------------------------------------------------------------------
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 5,
  duration: '2m',
  thresholds: {
    http_req_failed:   ['rate<0.05'],
    http_req_duration: ['p(95)<30000', 'p(99)<60000'],  // LLM calls are slow
  },
};

const BASE = __ENV.FORGEOS_BASE || 'http://localhost:5000';
const AGENT_ID = __ENV.TARGET_AGENT_ID || '';
const API_KEY = __ENV.FORGEOS_API_KEY || '';

if (!AGENT_ID) {
  throw new Error('TARGET_AGENT_ID env var is required');
}

const HEADERS = {
  'Content-Type': 'application/json',
  ...(API_KEY && { 'X-API-Key': API_KEY }),
};

const PROMPTS = [
  'What is the capital of France?',
  'Summarize the benefits of multi-tenant SaaS.',
  'List three ways to improve code review quality.',
  'Explain async/await to a junior developer.',
  'What metrics should an agent platform track?',
];

export default function () {
  const prompt = PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
  const body = JSON.stringify({ prompt, context: {} });

  const res = http.post(
    `${BASE}/api/platform/agents/${AGENT_ID}/invoke`,
    body,
    { headers: HEADERS, timeout: '90s' },
  );

  check(res, {
    '200 OK': (r) => r.status === 200,
    'has result': (r) => {
      try {
        const data = r.json();
        return data && (data.result || data.output);
      } catch {
        return false;
      }
    },
  });

  sleep(2);  // Don't hammer the LLM provider
}
