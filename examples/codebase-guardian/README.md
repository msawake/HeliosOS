# Codebase Guardian

Always-on GitHub PR security reviewer. Scans every pull request for hardcoded secrets, injection patterns, and XSS. Pages security lead for critical findings. 15 Helios OS runtime controls per iteration.

Uses Claude Sonnet for deep code review via Atlas Gateway.

## Run

```bash
PYTHONPATH=. \
GITHUB_REPO=your-org/your-repo \
ATLAS_GATEWAY_URL=https://your-gateway/v1 \
ATLAS_GATEWAY_KEY=sk-... \
python3 examples/codebase-guardian/agent.py
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | PR review loop — 15 numbered runtime controls |
| `agent_raw.py` | Same review, zero governance (for comparison) |
| `tools.py` | GitHub CLI wrappers + 18 security pattern detectors |
| `manifest.yaml` | Helios OS contract |
| `COMPARISON.md` | Side-by-side raw vs governed |
