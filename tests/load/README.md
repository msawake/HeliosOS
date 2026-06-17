# Helios OS Load Tests

[k6](https://k6.io/docs/) scripts for measuring Helios OS throughput under
realistic and adversarial conditions.

## Install k6

```bash
# macOS
brew install k6

# Linux
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6

# Docker (one-off)
docker run --rm -i -v $(pwd):/scripts grafana/k6 run /scripts/tests/load/smoke.js
```

## Tests

| File | Scenario | VUs | Duration | Purpose |
|------|----------|-----|----------|---------|
| `smoke.js` | Minimal sanity | 1 | 30s | Is the API alive? |
| `steady.js` | Sustained load | 20→100 | 5 min | Can we hold 100 RPS? |
| `spike.js` | Traffic burst | 10→500 | 5.5 min | Does HPA scale fast enough? |
| `invoke-agent.js` | LLM invocations | 5 | 2 min | End-to-end real agent calls |

## Running

### Against local dev
```bash
# Backend: cd /Users/jama/Documents/one && python -m src.bootstrap --no-auth
# Then in another terminal:
k6 run tests/load/smoke.js
```

### Against staging
```bash
FORGEOS_BASE=https://staging.forgeos.example.com k6 run tests/load/smoke.js
FORGEOS_BASE=https://staging.forgeos.example.com k6 run tests/load/steady.js
```

### Against a real agent invocation
```bash
# First deploy a REFLEX agent via the UI or API, then:
TARGET_AGENT_ID=abc123 \
FORGEOS_BASE=https://staging.forgeos.example.com \
FORGEOS_API_KEY=... \
  k6 run tests/load/invoke-agent.js
```

## Thresholds

Each script defines `thresholds` that cause exit code 1 when violated.
Use this in CI to gate deploys:

```yaml
# .github/workflows/load-test-on-pr.yml (example)
- run: FORGEOS_BASE=${{ env.STAGING_URL }} k6 run tests/load/smoke.js
```

## Reading results

```
checks.........................: 100.00% ✓ 5460 ✗ 0
http_req_duration..............: avg=87.2ms  p(95)=213ms p(99)=421ms
http_req_failed................: 0.00% ✓ 0    ✗ 2730
http_reqs......................: 2730   45.48/s
iteration_duration.............: avg=1.09s
vus............................: 1
```

Focus on:
- `http_req_failed` — must be < 1% for steady, < 5% for spike
- `http_req_duration p(95)` — the one you pick for SLOs
- `http_reqs /s` — your actual throughput

## Tuning

- **If smoke.js fails locally**: backend probably isn't running. Check
  `curl http://localhost:5000/api/health`.
- **If steady.js fails in staging**: scale up your replicas (`kubectl scale
  deployment forgeos-api --replicas=4`) and re-run.
- **If spike.js fails**: HPA is too slow. Tune
  `hpa-api.yaml:scaleUp.policies[*].periodSeconds` or increase `minReplicas`.
- **If invoke-agent.js times out**: LLM provider is slow. Increase k6
  request timeout or lower VUs.

## Safety

- **Never run spike/invoke-agent against prod.** Token costs + latency
  spikes are real. Always use a staging environment.
- **Set a budget**: LLM providers charge per token. A 2-minute invoke-agent
  test with 5 VUs can easily consume $5-20 in Claude credits.
- **Respect rate limits**: Anthropic + OpenAI throttle you. If k6 gets
  HTTP 429 spam, lower VUs.
