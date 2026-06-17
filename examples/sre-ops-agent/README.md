# SRE Ops Agent

Always-on autonomous infrastructure monitor. Checks service health every 2 minutes, detects anomalies, runs diagnostics with Claude, escalates critical issues to humans. 11 Helios OS runtime controls per iteration, ~4,300 per day.

## Run

```bash
PYTHONPATH=. \
FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
ANTHROPIC_API_KEY=sk-ant-... \
python3 examples/sre-ops-agent/agent.py
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Autonomous monitoring loop with Claude investigation + full runtime governance |
| `manifest.yaml` | Helios OS contract (always_on, $10/day budget, HITL for critical fixes) |
