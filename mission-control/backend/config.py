"""Environment configuration for Mission Control."""

import os

FORGEOS_API = os.environ.get("FORGEOS_API_URL", "http://localhost:5000")
FORGEOS_API_TOKEN = os.environ.get("FORGEOS_API_TOKEN", "")
MC_PASSWORD = os.environ.get("FORGEOS_MC_PASSWORD", "")
# Cloud Run injects PORT; honor it first, fall back to MC_PORT or 8888 for local.
PORT = int(os.environ.get("PORT") or os.environ.get("MC_PORT") or "8888")
COOKIE_NAME = "mc_session"
