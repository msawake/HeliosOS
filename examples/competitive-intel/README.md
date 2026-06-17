# Competitive Intelligence Agent

Dual-LLM research pipeline: Gemini Flash scans for data (fast, cheap), Claude Opus analyzes findings (deep reasoning). 13 Helios OS runtime governance checks per invocation.

## Run

```bash
PYTHONPATH=. \
ATLAS_GATEWAY_URL=https://your-gateway/v1 \
ATLAS_GATEWAY_KEY=sk-... \
python3 examples/competitive-intel/agent.py "Analyze competitor X strategy"
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | 3-phase pipeline (scan, analyze, recommend) with 13 runtime controls |
| `manifest.yaml` | Helios OS contract |
