"""
Kubeconfig-style portable config for the forgeos CLI.

A user can be handed a single YAML file (or have one at ~/.forgeos/config)
and the CLI will pick up the API URL + bearer token from it without needing
the repo on disk.

File format (one or many contexts):

    apiVersion: forgeos/v1
    kind: Config
    current-context: cloud-run
    contexts:
      - name: cloud-run
        url: https://forgeos-platform-api-meundhbn7a-ew.a.run.app
        token: "eyJhbGciOi..."
        # Optional:
        # auth-scheme: bearer | x-api-key   (default: bearer)
      - name: localhost
        url: http://localhost:5000
        token: ""
        auth-scheme: bearer

Resolution order (highest precedence first):

  1. --url / --api-key / --token  CLI flags
  2. FORGEOS_API_URL / FORGEOS_API_KEY env vars
  3. --config <path>              CLI flag
  4. $FORGEOS_CONFIG              env var
  5. ~/.forgeos/config            default

The shape is intentionally narrower than kubeconfig — no certs, no
namespaces, no exec auth plugins. Add fields as needed; treat unknown
fields as ignored so configs from newer CLIs don't break older ones.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_PATH = Path.home() / ".forgeos" / "config"


class ConfigError(Exception):
    pass


def _candidate_paths(explicit: str | None) -> list[Path]:
    if explicit:
        return [Path(explicit).expanduser()]
    env = os.environ.get("FORGEOS_CONFIG")
    if env:
        return [Path(env).expanduser()]
    return [DEFAULT_PATH]


def load(explicit_path: str | None = None) -> dict[str, Any] | None:
    """Read the config file. Returns None if no file is present and no
    explicit path was requested. Raises ConfigError when an explicit path
    is given but unreadable or malformed."""
    for p in _candidate_paths(explicit_path):
        if not p.exists():
            if explicit_path:
                raise ConfigError(f"config file not found: {p}")
            continue
        # Enforce 0600 like kubectl does for credentials files.
        try:
            mode = p.stat().st_mode & 0o777
            if mode & 0o077:
                raise ConfigError(
                    f"{p} is world-readable (mode {oct(mode)}); "
                    f"chmod 600 it and retry"
                )
        except FileNotFoundError:
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"could not parse {p}: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{p} must be a YAML mapping at the top level")
        return data
    return None


def _pick_context(data: dict[str, Any], name: str | None) -> dict[str, Any]:
    contexts = data.get("contexts") or []
    if not isinstance(contexts, list) or not contexts:
        # Flat shape — the top-level dict itself is the context.
        return {k: v for k, v in data.items() if k not in ("apiVersion", "kind")}
    target = name or data.get("current-context")
    if not target:
        # No current-context set; pick the first.
        return contexts[0]
    for c in contexts:
        if isinstance(c, dict) and c.get("name") == target:
            return c
    raise ConfigError(f"context '{target}' not found in config")


def resolve(
    *,
    cli_url: str | None,
    cli_token: str | None,
    context: str | None,
    config_path: str | None,
) -> tuple[str | None, str | None, str]:
    """Resolve the (url, token, scheme) tuple for an HTTP call.

    `cli_url` / `cli_token` win when set; otherwise env, otherwise file.
    `context` selects a named context inside the config file.
    """
    env_url = os.environ.get("FORGEOS_API_URL")
    env_token = os.environ.get("FORGEOS_API_KEY") or os.environ.get("FORGEOS_API_TOKEN")

    file_url = file_token = None
    scheme = "bearer"
    try:
        data = load(config_path)
    except ConfigError:
        if cli_url or env_url:
            data = None  # fall through if other sources can satisfy us
        else:
            raise
    if data:
        ctx = _pick_context(data, context)
        file_url = ctx.get("url") or ctx.get("server")
        file_token = ctx.get("token") or ctx.get("api-key") or ctx.get("apiKey")
        scheme = (ctx.get("auth-scheme") or ctx.get("authScheme") or "bearer").lower()

    url = cli_url or env_url or file_url
    token = cli_token or env_token or file_token
    return url, token, scheme
