# Contributing

See [docs/development/contributing.md](docs/development/contributing.md) for setup, tests, and project structure.

**Quick local loop:**

```bash
pip install -e ".[dev]"
cp .env.example .env
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
export FORGEOS_API_URL=http://localhost:5000
forgeos health
```

The Next.js dashboard is optional and lives outside this repo — see the contributing guide.