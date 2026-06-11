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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client import ForgeOSClient, ForgeOSError
from .manifest import AgentManifest


def _print_ok(msg: str):
    print(f"\033[32m✓\033[0m {msg}")


def _print_err(msg: str):
    print(f"\033[31m✗\033[0m {msg}", file=sys.stderr)


def _print_warn(msg: str):
    print(f"\033[33m!\033[0m {msg}")


def cmd_validate(args) -> int:
    """Validate an agent manifest without deploying."""
    try:
        path = Path(args.file)
        if path.suffix in (".yaml", ".yml"):
            manifest = AgentManifest.from_yaml(path)
        elif path.suffix == ".json":
            manifest = AgentManifest.from_json(path)
        else:
            _print_err(f"Unsupported file type: {path.suffix}")
            return 1

        # Also try to resolve system_prompt file references
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

        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            agent_id = client.deploy(path)
            _print_ok(f"Deployed agent: {agent_id}")
            return 0
    except (ForgeOSError, FileNotFoundError, ValueError) as e:
        _print_err(f"Deploy failed: {e}")
        return 1


def _deploy_team(args, data: dict) -> int:
    """Deploy a team manifest via the API."""
    from .manifest import TeamManifest
    team = TeamManifest.from_dict(data)
    with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
        resp = client._session.post(f"{client._base_url}/api/platform/teams", json=data)
        if resp.status_code in (200, 201):
            result = resp.json()
            agent_ids = result.get("agent_ids", [])
            _print_ok(f"Team '{team.metadata.name}' deployed: {len(agent_ids)} agents")
            for aid in agent_ids:
                print(f"  - {aid}")
            return 0
        else:
            _print_err(f"Team deploy failed: {resp.status_code} — {resp.text}")
            return 1


def cmd_undeploy_team(args) -> int:
    """Undeploy all agents in a team."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            resp = client._session.delete(
                f"{client._base_url}/api/platform/teams/{args.namespace}/{args.name}"
            )
            if resp.status_code == 200:
                result = resp.json()
                _print_ok(f"Team '{args.name}' undeployed: {result.get('removed', 0)} agents removed")
                return 0
            elif resp.status_code == 404:
                _print_err(f"Team '{args.namespace}/{args.name}' not found")
                return 1
            else:
                _print_err(f"Undeploy failed: {resp.status_code} — {resp.text}")
                return 1
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_list(args) -> int:
    """List deployed agents."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            agents = client.list()
            if args.json:
                print(json.dumps(agents, indent=2, default=str))
                return 0
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
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_invoke(args) -> int:
    """Invoke an agent with a prompt."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            result = client.invoke(args.agent_id, args.prompt)
            print(json.dumps(result, indent=2, default=str))
            if result.get("warnings"):
                for w in result["warnings"]:
                    _print_warn(w)
            return 0
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_undeploy(args) -> int:
    """Undeploy an agent."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            client.undeploy(args.agent_id)
            _print_ok(f"Undeployed {args.agent_id}")
            return 0
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_credentials_put(args) -> int:
    """Store a per-user credential (write-only; no read endpoint exists)."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            result = client._request(
                "POST",
                f"/api/credentials/{args.kind}",
                json={"pat": args.pat, "user_id": args.user_id},
            )
            _print_ok(f"Stored {args.kind} credential for user_id={result.get('user_id')}")
            return 0
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_answer(args) -> int:
    """Respond to a pending A2H request with text, value, or choice."""
    if not args.text and args.value is None:
        _print_err("Provide --text or --value")
        return 1
    response: dict = {}
    if args.text is not None:
        response["text"] = args.text
    if args.value is not None:
        response["value"] = args.value
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            result = client._request(
                "POST",
                f"/api/a2h/requests/{args.request_id}/respond",
                json={
                    "response": response,
                    "responded_by": args.responded_by,
                    "channel": "cli",
                },
            )
            _print_ok(f"Responded to {args.request_id}: {result}")
            return 0
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def cmd_health(args) -> int:
    """Check platform health."""
    try:
        with ForgeOSClient(base_url=args.url, api_key=args.api_key) as client:
            h = client.health()
            print(json.dumps(h, indent=2))
            return 0
    except ForgeOSError as e:
        _print_err(str(e))
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forgeos",
        description="ForgeOS CLI — declare and manage agents from the command line",
    )
    parser.add_argument("--url", help="API base URL (overrides --config / env)")
    parser.add_argument("--api-key", help="Bearer token / X-API-Key value (overrides --config / env)")
    parser.add_argument("--config", help="Path to a forgeos config file (default: $FORGEOS_CONFIG or ~/.forgeos/config)")
    parser.add_argument("--context", help="Name of a context inside the config file")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a manifest without deploying")
    p_validate.add_argument("file", help="Path to agent.yaml or agent.json")
    p_validate.set_defaults(func=cmd_validate)

    p_deploy = sub.add_parser("deploy", help="Deploy an agent from a manifest")
    p_deploy.add_argument("file", help="Path to agent.yaml or agent.json")
    p_deploy.set_defaults(func=cmd_deploy)

    p_list = sub.add_parser("list", help="List deployed agents")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON")
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

    from . import mc_cli
    mc_cli.register(sub)

    # `forgeos credentials put github --pat <PAT> [--user-id <id>]`
    p_creds = sub.add_parser("credentials", help="Manage per-user credentials (write-only)")
    creds_sub = p_creds.add_subparsers(dest="creds_cmd", required=True)
    p_creds_put = creds_sub.add_parser("put", help="Store a credential")
    p_creds_put.add_argument("kind", choices=["github"], help="Credential kind")
    p_creds_put.add_argument("--pat", required=True, help="Personal access token")
    p_creds_put.add_argument("--user-id", default="default", help="User identifier (default: 'default')")
    p_creds_put.set_defaults(func=cmd_credentials_put)

    # `forgeos answer <request_id> --text "..."` or --value "..."
    p_answer = sub.add_parser("answer", help="Answer a pending A2H request with text/value/choice")
    p_answer.add_argument("request_id")
    p_answer.add_argument("--text", help="Freeform text response (response_type=text)")
    p_answer.add_argument("--value", help="Structured value (response_type=choice/number)")
    p_answer.add_argument("--responded-by", default="cli", help="Who is responding")
    p_answer.set_defaults(func=cmd_answer)

    args = parser.parse_args(argv)
    # Resolve url/token from CLI flags → env → config file (kubectl-style).
    try:
        from .config_file import resolve as _resolve_cfg
        url, token, _scheme = _resolve_cfg(
            cli_url=args.url,
            cli_token=args.api_key,
            context=args.context,
            config_path=args.config,
        )
        args.url = url
        args.api_key = token
    except Exception as e:
        _print_err(str(e))
        return 2
    if not args.url:
        _print_err("no API URL: set --url, FORGEOS_API_URL, or a config file with `url:`")
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
