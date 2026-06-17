"""
Helios OS Agent Deployment Framework.

Scans examples/ for YAML manifests, validates, deploys, and reports.

Usage:
    PYTHONPATH=. python examples/deploy.py                    # deploy all
    PYTHONPATH=. python examples/deploy.py --category a2a     # one category
    PYTHONPATH=. python examples/deploy.py --stack forgeos    # one stack
    PYTHONPATH=. python examples/deploy.py --dry-run          # validate only
    PYTHONPATH=. python examples/deploy.py --clean            # undeploy all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.forgeos_sdk import AgentManifest, ForgeOSClient

EXAMPLES_DIR = Path("examples")
CATEGORIES = ["forgeos", "crewai", "adk", "openclaw", "a2a", "platform", "filesystem", "mixed", "advanced"]


def find_manifests(category=None, stack=None):
    """Find all YAML manifests, optionally filtered."""
    results = []
    dirs = [EXAMPLES_DIR / c for c in CATEGORIES] if not category else [EXAMPLES_DIR / category]
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                m = AgentManifest.from_yaml(f)
                if stack and m.spec.stack != stack:
                    continue
                results.append((d.name, f, m))
            except Exception as e:
                results.append((d.name, f, e))
    return results


def deploy_all(args):
    manifests = find_manifests(category=args.category, stack=args.stack)
    print(f"Found {len(manifests)} manifests\n")
    print(f"{'CAT':<12} {'NAME':<40} {'STACK':<10} {'TYPE':<14} {'STATUS':<10}")
    print("─" * 90)

    ok = warn = fail = 0
    client = ForgeOSClient(base_url="http://localhost:5000") if not args.dry_run else None

    for cat, path, manifest in manifests:
        if isinstance(manifest, Exception):
            print(f"{cat:<12} {path.stem:<40} {'?':<10} {'?':<14} PARSE_ERR: {manifest}")
            fail += 1
            continue

        name = manifest.metadata.name
        stack = manifest.spec.stack
        exec_type = manifest.spec.execution_type

        if args.dry_run:
            print(f"{cat:<12} {name:<40} {stack:<10} {exec_type:<14} VALID")
            ok += 1
            continue

        try:
            agent_id = client.deploy(manifest, base_path=path.parent)
            print(f"{cat:<12} {name:<40} {stack:<10} {exec_type:<14} OK ({agent_id})")
            ok += 1
        except Exception as e:
            if "already exists" in str(e):
                print(f"{cat:<12} {name:<40} {stack:<10} {exec_type:<14} EXISTS")
                warn += 1
            else:
                print(f"{cat:<12} {name:<40} {stack:<10} {exec_type:<14} FAIL: {str(e)[:40]}")
                fail += 1

    if client:
        client.close()
    print(f"\n{'=' * 60}")
    label = "Validated" if args.dry_run else "Deployed"
    print(f"  {label}: {ok} OK, {warn} EXISTS, {fail} FAIL (total: {len(manifests)})")
    print(f"{'=' * 60}")
    return 1 if fail > 0 else 0


def clean_all(args):
    client = ForgeOSClient(base_url="http://localhost:5000")
    agents = client.list()
    example_agents = [a for a in agents if a.get("department") == "examples"]
    print(f"Found {len(example_agents)} example agents to undeploy\n")
    for a in example_agents:
        try:
            client.undeploy(a["agent_id"])
            print(f"  Undeployed: {a['name']} ({a['agent_id']})")
        except Exception as e:
            print(f"  Failed: {a['name']} — {e}")
    client.close()
    print(f"\nCleaned {len(example_agents)} agents.")


def main():
    parser = argparse.ArgumentParser(description="Helios OS Agent Deployment Framework")
    parser.add_argument("--category", choices=CATEGORIES, help="Deploy one category only")
    parser.add_argument("--stack", choices=["forgeos", "crewai", "adk", "openclaw"], help="Filter by stack")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifests without deploying")
    parser.add_argument("--clean", action="store_true", help="Undeploy all example agents")
    args = parser.parse_args()

    if args.clean:
        clean_all(args)
    else:
        sys.exit(deploy_all(args))


if __name__ == "__main__":
    main()
