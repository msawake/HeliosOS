# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Local config and credential store at ``~/.forgeos/``.

Layout (kubectl-style):

    ~/.forgeos/
        config.yaml      # current context, default profile, UI preferences
        credentials      # mode 0600; YAML mapping of credential-name -> value

Credentials are stored as plaintext under file permissions ``0600``. This
matches the kubectl / aws-cli convention and avoids an OS-keyring dependency
for what is meant to be a local-only thin client. The store enforces the
permissions on read: if the file is world- or group-readable it raises
loudly rather than silently leaking secrets to other local users.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR_ENV = "FORGEOS_CONFIG_DIR"


class CredentialsPermissionError(RuntimeError):
    """Raised when ``~/.forgeos/credentials`` is readable by other users."""


def config_dir() -> Path:
    """Return the config directory, honouring ``FORGEOS_CONFIG_DIR`` if set."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".forgeos"


def _config_path() -> Path:
    return config_dir() / "config.yaml"


def _credentials_path() -> Path:
    return config_dir() / "credentials"


def _ensure_dir() -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except PermissionError:
        # On filesystems that don't support chmod (rare), the read-side
        # check below will still catch insecure permissions.
        pass


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = yaml.safe_load(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping in {path}, got {type(data).__name__}")
    return data


def _write_yaml(path: Path, data: dict[str, Any], *, mode: int) -> None:
    _ensure_dir()
    # Write atomically: tmp file -> rename.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    try:
        tmp.chmod(mode)
    except PermissionError:
        pass
    tmp.replace(path)


# ---- Public API: config.yaml -----------------------------------------------


def load_config() -> dict[str, Any]:
    """Return the parsed ``config.yaml`` (or an empty dict)."""
    return _load_yaml(_config_path())


def save_config(data: dict[str, Any]) -> None:
    _write_yaml(_config_path(), data, mode=0o644)


def set_config_value(key: str, value: Any) -> None:
    """Set a top-level key in ``config.yaml``."""
    data = load_config()
    data[key] = value
    save_config(data)


def get_config_value(key: str, default: Any = None) -> Any:
    return load_config().get(key, default)


def current_profile() -> str:
    """Return the active profile name (default: ``"default"``)."""
    return load_config().get("current_profile", "default")


def set_current_profile(name: str) -> None:
    set_config_value("current_profile", name)


# ---- Public API: credentials ----------------------------------------------


def _check_credentials_permissions(path: Path) -> None:
    """Raise if the credentials file is readable by group/other.

    This is the kubectl/aws-cli safety net: if a user copies their config
    out of the secure home directory and forgets to lock it down, fail
    loudly instead of leaking secrets to every other local user.
    """
    if not path.exists():
        return
    st = path.stat()
    bad_bits = st.st_mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH)
    if bad_bits:
        raise CredentialsPermissionError(
            f"{path} has insecure permissions {oct(st.st_mode & 0o777)}. "
            f"Run: chmod 600 {path}"
        )


def load_credentials() -> dict[str, Any]:
    """Return the full credentials file contents (profile -> {name: value})."""
    path = _credentials_path()
    _check_credentials_permissions(path)
    return _load_yaml(path)


def save_credentials(data: dict[str, Any]) -> None:
    _write_yaml(_credentials_path(), data, mode=0o600)


def get_credential(name: str, *, profile: str | None = None) -> str | None:
    """Look up a single credential by name in the active (or named) profile."""
    profile = profile or current_profile()
    creds = load_credentials()
    bucket = creds.get(profile)
    if not isinstance(bucket, dict):
        return None
    val = bucket.get(name)
    return str(val) if val is not None else None


def set_credential(name: str, value: str, *, profile: str | None = None) -> None:
    profile = profile or current_profile()
    creds = load_credentials()
    bucket = creds.setdefault(profile, {})
    if not isinstance(bucket, dict):
        raise ValueError(f"profile {profile!r} is not a mapping in credentials")
    bucket[name] = value
    save_credentials(creds)


def delete_credential(name: str, *, profile: str | None = None) -> bool:
    profile = profile or current_profile()
    creds = load_credentials()
    bucket = creds.get(profile)
    if not isinstance(bucket, dict) or name not in bucket:
        return False
    del bucket[name]
    save_credentials(creds)
    return True


def list_credentials(*, profile: str | None = None) -> list[str]:
    """Return the *names* (not values) of credentials in the given profile."""
    profile = profile or current_profile()
    creds = load_credentials()
    bucket = creds.get(profile)
    if not isinstance(bucket, dict):
        return []
    return sorted(bucket.keys())


# ---- Env-aware credential resolver ----------------------------------------


def resolve_credential(name: str, *, profile: str | None = None) -> str | None:
    """Look up a credential first in the environment, then in the store.

    Environment wins so that CI / one-off overrides still work without
    rewriting ``~/.forgeos/credentials``. Use this from code paths that
    used to call ``os.environ.get(NAME)``.
    """
    env_val = os.environ.get(name)
    if env_val:
        return env_val
    try:
        return get_credential(name, profile=profile)
    except CredentialsPermissionError:
        # Propagate — silent fallback would defeat the whole point of the
        # permission check.
        raise
    except Exception:
        return None
