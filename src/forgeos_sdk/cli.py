# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
ForgeOS CLI.

Usage:
    forgeos deploy ./agent.yaml
    forgeos validate ./agent.yaml
    forgeos list
    forgeos invoke <agent_id> "your prompt"
    forgeos undeploy <agent_id>
    forgeos health

By default every command runs in-process against the platform Python
objects. Pass ``--remote <url>`` to keep using the legacy HTTP Mission
Control backend instead (deprecated; removed in a follow-up).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .manifest import AgentManifest


def _print_ok(msg: str):
    print(f"\033[32m✓\033[0m {msg}")


def _print_err(msg: str):
    print(f"\033[31m✗\033[0m {msg}", file=sys.stderr)


def _print_warn(msg: str):
    print(f"\033[33m!\033[0m {msg}")


def _open_client(args):
    """Return a context-managed client.

    Local (in-process) by default. ``--remote URL`` (or
    ``FORGEOS_REMOTE_URL`` env) selects the legacy HTTP client.
    """
    remote = getattr(args, "remote", None) or os.environ.get("FORGEOS_REMOTE_URL")
    if remote:
        from .client import ForgeOSClient

        return ForgeOSClient(base_url=remote, api_key=getattr(args, "api_key", None))
    from .local_runtime import LocalClient

    return LocalClient()


def _client_error_types():
    """Return the tuple of exception classes that mean 'CLI surface error'."""
    try:
        from .client import ForgeOSError

        return (ForgeOSError, FileNotFoundError, ValueError, RuntimeError)
    except Exception:
        return (FileNotFoundError, ValueError, RuntimeError)


def cmd_validate(args) -> int:
    """Validate an agent manifest without deploying. No platform boot needed."""
    try:
        path = Path(args.file)
        if path.suffix in (".yaml", ".yml"):
            manifest = AgentManifest.from_yaml(path)
        elif path.suffix == ".json":
            manifest = AgentManifest.from_json(path)
        else:
            _print_err(f"Unsupported file type: {path.suffix}")
            return 1

        deploy_body = manifest.to_deploy_request(base_path=path.parent)
        _print_ok(f"Manifest valid: {manifest.metadata.name}")
        print(f"  Stack:          {manifest.spec.stack}")
        print(f"  Execution type: {manifest.spec.execution_type}")
        print(f"  Ownership:      {manifest.spec.ownership}")
        print(f"  Model:          {manifest.spec.llm.chat_model} ({manifest.spec.llm.provider})")
        if manifest.spec.schedule:
            print(f"  Schedule:       {manifest.spec.schedule}")
        if manifest.spec.event_triggers:
            print(f"  Events:         {manifest.spec.event_triggers}")
        if manifest.spec.tools:
            print(f"  Tools:          {len(manifest.spec.tools)} ({', '.join(manifest.spec.tools[:3])}...)")
        print(f"  System prompt:  {len(deploy_body['system_prompt'])} chars")
        return 0
    except Exception as e:
        _print_err(f"Validation failed: {e}")
        return 1


def cmd_deploy(args) -> int:
    """Deploy an agent or team from a manifest file."""
    path = Path(args.file)
    try:
        import yaml

        data = yaml.safe_load(path.read_text())
        kind = data.get("kind", "Agent")
        if kind == "Team":
            return _deploy_team(args, data)

        with _open_client(args) as client:
            agent_id = client.deploy(path)
            _print_ok(f"Deployed agent: {agent_id}")
            return 0
    except _client_error_types() as e:
        _print_err(f"Deploy failed: {e}")
        return 1


def _deploy_team(args, data: dict) -> int:
    """Deploy a team manifest.

    Team deploy still goes through the HTTP path because the in-process
    LocalClient does not expose a team endpoint yet. Until that lands,
    fall back to the legacy client when a Team manifest is detected.
    """
    from .client import ForgeOSClient
    from .manifest import TeamManifest

    team = TeamManifest.from_dict(data)
    remote = getattr(args, "remote", None) or os.environ.get("FORGEOS_REMOTE_URL")
    if not remote:
        _print_err(
            "Team deploys still require --remote <url> until the in-process "
            "team path lands. Pass --remote http://localhost:5000 to use the "
            "legacy HTTP backend, or split the team into individual agents."
        )
        return 1
    with ForgeOSClient(base_url=remote, api_key=getattr(args, "api_key", None)) as client:
        resp = client._http.post("/api/platform/teams", json=data)
        if resp.status_code in (200, 201):
            result = resp.json()
            agent_ids = result.get("agent_ids", [])
            _print_ok(f"Team '{team.metadata.name}' deployed: {len(agent_ids)} agents")
            for aid in agent_ids:
                print(f"  - {aid}")
            return 0
        _print_err(f"Team deploy failed: {resp.status_code} — {resp.text}")
        return 1


def cmd_undeploy_team(args) -> int:
    """Undeploy all agents in a team (HTTP only for now — see _deploy_team)."""
    from .client import ForgeOSClient

    remote = getattr(args, "remote", None) or os.environ.get("FORGEOS_REMOTE_URL")
    if not remote:
        _print_err(
            "undeploy-team still requires --remote <url>. Pass "
            "--remote http://localhost:5000 to use the legacy HTTP backend."
        )
        return 1
    try:
        with ForgeOSClient(base_url=remote, api_key=getattr(args, "api_key", None)) as client:
            resp = client._http.delete(
                f"/api/platform/teams/{args.namespace}/{args.name}"
            )
            if resp.status_code == 200:
                result = resp.json()
                _print_ok(
                    f"Team '{args.name}' undeployed: "
                    f"{result.get('removed', 0)} agents removed"
                )
                return 0
            if resp.status_code == 404:
                _print_err(f"Team '{args.namespace}/{args.name}' not found")
                return 1
            _print_err(f"Undeploy failed: {resp.status_code} — {resp.text}")
            return 1
    except _client_error_types() as e:
        _print_err(str(e))
        return 1


def cmd_list(args) -> int:
    try:
        with _open_client(args) as client:
            agents = client.list()
            if not agents:
                _print_warn("No agents deployed")
                return 0
            print(f"{'AGENT_ID':14}  {'NAME':30}  {'STACK':10}  {'TYPE':14}  {'STATUS':12}")
            print(f"{'-'*14}  {'-'*30}  {'-'*10}  {'-'*14}  {'-'*12}")
            for a in agents:
                print(
                    f"{a['agent_id']:14}  {a['name']:30}  {a.get('stack','?'):10}  "
                    f"{a.get('execution_type','?'):14}  {a.get('status','?'):12}"
                )
            return 0
    except _client_error_types() as e:
        _print_err(str(e))
        return 1


def cmd_invoke(args) -> int:
    try:
        with _open_client(args) as client:
            result = client.invoke(args.agent_id, args.prompt)
            print(json.dumps(result, indent=2, default=str))
            if isinstance(result, dict) and result.get("warnings"):
                for w in result["warnings"]:
                    _print_warn(w)
            return 0
    except _client_error_types() as e:
        _print_err(str(e))
        return 1


def cmd_undeploy(args) -> int:
    try:
        with _open_client(args) as client:
            client.undeploy(args.agent_id)
            _print_ok(f"Undeployed {args.agent_id}")
            return 0
    except _client_error_types() as e:
        _print_err(str(e))
        return 1


def cmd_health(args) -> int:
    try:
        with _open_client(args) as client:
            h = client.health()
            print(json.dumps(h, indent=2))
            return 0
    except _client_error_types() as e:
        _print_err(str(e))
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forgeos",
        description="ForgeOS CLI — declare and manage agents from the command line",
    )
    parser.add_argument(
        "--remote",
        help=(
            "Use the legacy HTTP Mission Control backend at this URL instead "
            "of in-process. Default: in-process (no server required)."
        ),
    )
    parser.add_argument("--api-key", help="API key for --remote (default: FORGEOS_API_KEY)")
    # Back-compat: --url is the old name for --remote.
    parser.add_argument("--url", dest="remote", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a manifest without deploying")
    p_validate.add_argument("file", help="Path to agent.yaml or agent.json")
    p_validate.set_defaults(func=cmd_validate)

    p_deploy = sub.add_parser("deploy", help="Deploy an agent from a manifest")
    p_deploy.add_argument("file", help="Path to agent.yaml or agent.json")
    p_deploy.set_defaults(func=cmd_deploy)

    p_list = sub.add_parser("list", help="List deployed agents")
    p_list.set_defaults(func=cmd_list)

    p_invoke = sub.add_parser("invoke", help="Invoke an agent with a prompt")
    p_invoke.add_argument("agent_id")
    p_invoke.add_argument("prompt")
    p_invoke.set_defaults(func=cmd_invoke)

    p_undeploy = sub.add_parser("undeploy", help="Undeploy an agent")
    p_undeploy.add_argument("agent_id")
    p_undeploy.set_defaults(func=cmd_undeploy)

    p_health = sub.add_parser("health", help="Platform health check")
    p_health.set_defaults(func=cmd_health)

    p_undeploy_team = sub.add_parser("undeploy-team", help="Undeploy all agents in a team")
    p_undeploy_team.add_argument("name", help="Team name")
    p_undeploy_team.add_argument("--namespace", default="default", help="Team namespace")
    p_undeploy_team.set_defaults(func=cmd_undeploy_team)

    # `config` subcommands — added in chunk 2; import is optional so the
    # CLI keeps working before that module lands.
    try:
        from . import config_cli  # type: ignore[attr-defined]

        config_cli.register(sub)
    except ImportError:
        pass

    from . import mc_cli

    mc_cli.register(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
