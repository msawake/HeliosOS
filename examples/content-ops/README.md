# Content Operations Pipeline

Multi-client content production with namespace isolation. Gemini Flash produces, Claude Sonnet reviews. 12 Helios OS runtime controls per content piece.

3 clients with different rules: PharmaCo (HIPAA, no AI images), FinTech (SEC/FINRA), ShopWave (unregulated).

## Run

```bash
PYTHONPATH=. \
ATLAS_GATEWAY_URL=https://your-gateway/v1 \
ATLAS_GATEWAY_KEY=sk-... \
python3 examples/content-ops/agent.py
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Full pipeline — 12 numbered runtime controls per piece |
| `agent_raw.py` | Same pipeline, zero governance (for comparison) |
| `clients.py` | Per-client config: brand voice, compliance rules, budget |
| `manifest.yaml` | Helios OS contract |
| `COMPARISON.md` | Side-by-side raw vs governed |
