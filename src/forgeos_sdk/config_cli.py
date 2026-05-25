# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
`forgeos config` subcommand group.

Manages local config and credentials under ``~/.forgeos/``. Kubectl-style:

    forgeos config view
    forgeos config set-credential ANTHROPIC_API_KEY sk-...
    forgeos config get-credential ANTHROPIC_API_KEY
    forgeos config delete-credential ANTHROPIC_API_KEY
    forgeos config list-credentials
    forgeos config use-profile staging
    forgeos config current-profile
"""

from __future__ import annotations

import sys

from . import config_store


def _err(msg: str) -> None:
    print(f"\033[31m✗\033[0m {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"\033[32m✓\033[0m {msg}")


def cmd_view(args) -> int:
    cfg = config_store.load_config()
    try:
        creds = config_store.load_credentials()
    except config_store.CredentialsPermissionError as e:
        _err(str(e))
        return 1
    redacted = {
        profile: {name: "***" for name in (bucket or {})}
        for profile, bucket in creds.items()
    }
    import yaml

    print("# ~/.forgeos/config.yaml")
    print(yaml.safe_dump(cfg, sort_keys=True) or "{}")
    print(f"# {config_store._credentials_path()} (values redacted)")
    print(yaml.safe_dump(redacted, sort_keys=True) or "{}")
    return 0


def cmd_set_credential(args) -> int:
    config_store.set_credential(args.name, args.value, profile=args.profile)
    profile = args.profile or config_store.current_profile()
    _ok(f"Stored credential {args.name!r} in profile {profile!r}")
    return 0


def cmd_get_credential(args) -> int:
    try:
        val = config_store.get_credential(args.name, profile=args.profile)
    except config_store.CredentialsPermissionError as e:
        _err(str(e))
        return 1
    if val is None:
        _err(f"Credential {args.name!r} not found")
        return 1
    print(val)
    return 0


def cmd_delete_credential(args) -> int:
    if config_store.delete_credential(args.name, profile=args.profile):
        profile = args.profile or config_store.current_profile()
        _ok(f"Deleted credential {args.name!r} from profile {profile!r}")
        return 0
    _err(f"Credential {args.name!r} not found")
    return 1


def cmd_list_credentials(args) -> int:
    try:
        names = config_store.list_credentials(profile=args.profile)
    except config_store.CredentialsPermissionError as e:
        _err(str(e))
        return 1
    profile = args.profile or config_store.current_profile()
    if not names:
        print(f"# no credentials in profile {profile!r}")
        return 0
    print(f"# profile: {profile}")
    for n in names:
        print(n)
    return 0


def cmd_use_profile(args) -> int:
    config_store.set_current_profile(args.name)
    _ok(f"Active profile is now {args.name!r}")
    return 0


def cmd_current_profile(args) -> int:
    print(config_store.current_profile())
    return 0


def register(sub) -> None:
    """Attach `forgeos config <subcommand>` to the main parser."""
    p_config = sub.add_parser("config", help="Manage ~/.forgeos config and credentials")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)

    p_view = config_sub.add_parser("view", help="Print config + redacted credentials")
    p_view.set_defaults(func=cmd_view)

    def _add_profile_arg(p):
        p.add_argument("--profile", help="Profile name (default: current)")

    p_set = config_sub.add_parser("set-credential", help="Store a credential")
    p_set.add_argument("name")
    p_set.add_argument("value")
    _add_profile_arg(p_set)
    p_set.set_defaults(func=cmd_set_credential)

    p_get = config_sub.add_parser("get-credential", help="Print a stored credential")
    p_get.add_argument("name")
    _add_profile_arg(p_get)
    p_get.set_defaults(func=cmd_get_credential)

    p_del = config_sub.add_parser("delete-credential", help="Delete a stored credential")
    p_del.add_argument("name")
    _add_profile_arg(p_del)
    p_del.set_defaults(func=cmd_delete_credential)

    p_ls = config_sub.add_parser("list-credentials", help="List credential names")
    _add_profile_arg(p_ls)
    p_ls.set_defaults(func=cmd_list_credentials)

    p_use = config_sub.add_parser("use-profile", help="Switch the active profile")
    p_use.add_argument("name")
    p_use.set_defaults(func=cmd_use_profile)

    p_cur = config_sub.add_parser("current-profile", help="Show the active profile")
    p_cur.set_defaults(func=cmd_current_profile)
