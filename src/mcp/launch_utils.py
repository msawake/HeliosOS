"""Shared helpers for launching stdio MCP servers.

Both MCP launch paths — the boot/platform manager (``server_manager.py``) and the
per-client manager (``client_mcp_manager.py``) — spawn stdio MCP packages and
resolve their environment. These helpers keep the two in sync for:

* **launcher selection** — npm packages run via ``npx``, PyPI packages via
  ``uvx``. The ``mcp-server-*`` name is used on BOTH registries, so an explicit
  ``npm:``/``pypi:`` prefix on the package lets an operator disambiguate.
* **GCP credential materialization** — Google client libraries authenticate via
  Application Default Credentials, which read a key *file* pointed at by
  ``GOOGLE_APPLICATION_CREDENTIALS``. The credential store only yields secrets as
  strings, so a service-account JSON arrives as an env *value* (e.g. a
  ``GOOGLE_APPLICATION_CREDENTIALS_JSON`` secret). We write it to a stable 0600
  temp file and point ``GOOGLE_APPLICATION_CREDENTIALS`` at it so BigQuery /
  Drive / Vertex MCP servers can authenticate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def resolve_launch_command(package: str, extra_args: list | None = None) -> tuple[str, list]:
    """Return the ``(command, args)`` to spawn a stdio MCP ``package``.

    Explicit runtime prefixes win and are stripped:
      * ``npm:<pkg>`` / ``npx:<pkg>``   -> ``npx -y <pkg>``
      * ``pypi:<pkg>`` / ``uvx:<pkg>``  -> ``uvx <pkg>``

    Otherwise the historical heuristic (unchanged, for backward compatibility):
    a scoped (``@scope/...``) or ``mcp-server-*`` name is treated as npm; anything
    else is a PyPI package launched via ``uvx``.
    """
    extra = list(extra_args or [])
    p = (package or "").strip()
    low = p.lower()
    if low.startswith(("npm:", "npx:")):
        return "npx", ["-y", p.split(":", 1)[1]] + extra
    if low.startswith(("pypi:", "uvx:")):
        return "uvx", [p.split(":", 1)[1]] + extra
    if p.startswith("@") or p.startswith("mcp-server-"):
        return "npx", ["-y", p] + extra
    return "uvx", [p] + extra


def _looks_like_sa_json(value: str) -> bool:
    s = (value or "").strip()
    if not s.startswith("{"):
        return False
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return False
    return isinstance(parsed, dict) and parsed.get("type") == "service_account"


def materialize_gcp_credentials(env: dict) -> dict:
    """Point ``GOOGLE_APPLICATION_CREDENTIALS`` at a key file when the env carries
    a service-account JSON as a string value (mutates and returns ``env``).

    Triggers when a ``GOOGLE_APPLICATION_CREDENTIALS_JSON`` var — or any value that
    parses as service-account JSON — is present and ``GOOGLE_APPLICATION_CREDENTIALS``
    is not already an existing file. The file is written 0600 under the temp dir
    with a content-hash name, so repeated connects reuse it (no unbounded leak).
    """
    if not isinstance(env, dict):
        return env
    existing = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if existing and os.path.isfile(existing):
        return env

    candidate = env.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not (isinstance(candidate, str) and _looks_like_sa_json(candidate)):
        candidate = None
        for val in env.values():
            if isinstance(val, str) and _looks_like_sa_json(val):
                candidate = val
                break
    if candidate is None:
        return env

    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"forgeos-gcp-cred-{digest}.json")
    try:
        if not os.path.exists(path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(candidate)
        env["GOOGLE_APPLICATION_CREDENTIALS"] = path
    except OSError as e:
        logger.warning("Could not materialize GCP credentials file: %s", e)
    return env
