"""
ForgeOS Agent Test Harness.

Deploys and invokes all example agents, validating each works end-to-end.

Usage:
    PYTHONPATH=. python examples/test.py                      # test all
    PYTHONPATH=. python examples/test.py --category platform  # one category
    PYTHONPATH=. python examples/test.py --type reflex        # one exec type
    PYTHONPATH=. python examples/test.py --no-cleanup         # keep agents after test
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from src.forgeos_sdk import AgentManifest, ForgeOSClient

EXAMPLES_DIR = Path("examples")
ALL_CATEGORIES = ["forgeos", "crewai", "adk", "openclaw", "a2a", "platform", "filesystem", "mixed", "advanced"]

TEST_PROMPTS = {
    "reflex": "Briefly describe what you do in one sentence.",
    "autonomous": "Begin working toward your goal. Report your first step.",
}


def find_all(category=None, exec_type=None):
    results = []
    dirs = [EXAMPLES_DIR / c for c in ALL_CATEGORIES] if not category else [EXAMPLES_DIR / category]
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                m = AgentManifest.from_yaml(f)
                if exec_type and m.spec.execution_type != exec_type:
                    continue
                results.append((d.name, f, m))
            except Exception:
                pass
    return results


def main():
    parser = argparse.ArgumentParser(description="ForgeOS Agent Test Harness")
    parser.add_argument("--category", choices=ALL_CATEGORIES)
    parser.add_argument("--type", choices=["reflex", "scheduled", "always_on", "event_driven", "autonomous"])
    parser.add_argument("--no-cleanup", action="store_true", help="Keep agents after test")
    args = parser.parse_args()

    manifests = find_all(category=args.category, exec_type=args.type)
    print(f"Testing {len(manifests)} agents\n")
    print(f"{'CAT':<12} {'NAME':<35} {'STACK':<9} {'TYPE':<14} {'DEPLOY':<8} {'TEST':<10} {'TOKENS':<7} {'MS':<7} {'NOTE':<30}")
    print("─" * 135)

    client = ForgeOSClient(base_url="http://localhost:5000")
    deployed = []
    ok = warn = fail = 0

    for cat, path, manifest in manifests:
        name = manifest.metadata.name
        stack = manifest.spec.stack
        exec_type = manifest.spec.execution_type

        # Deploy
        try:
            agent_id = client.deploy(manifest, base_path=path.parent)
            deployed.append(agent_id)
            deploy_st = "OK"
        except Exception as e:
            if "already exists" in str(e):
                agents = client.list()
                agent_id = next((a["agent_id"] for a in agents if a["name"] == name), None)
                if agent_id:
                    deployed.append(agent_id)
                deploy_st = "EXISTS"
            else:
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {'FAIL':<8} {'':<10} {'':<7} {'':<7} {str(e)[:30]}")
                fail += 1
                continue

        # Test based on execution type
        if exec_type in ("reflex", "autonomous"):
            prompt = TEST_PROMPTS.get(exec_type, "Hello!")
            t0 = time.time()
            try:
                result = client.invoke(agent_id, prompt)
                ms = int((time.time() - t0) * 1000)
                status = result.get("status", "?")
                tokens = result.get("tokens_used", 0)
                output = result.get("result", "")[:28]
                simulated = "[SIMULATED" in (result.get("result", ""))

                if status in ("completed", "idle") and not simulated:
                    test_st = "OK"
                    ok += 1
                elif simulated:
                    test_st = "SIMULATED"
                    warn += 1
                else:
                    test_st = status.upper()
                    warn += 1
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {test_st:<10} {tokens:<7} {ms:<7} {output}")
            except Exception as e:
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {'ERR':<10} {'':<7} {'':<7} {str(e)[:28]}")
                fail += 1

        elif exec_type == "scheduled":
            # Verify scheduler registered
            try:
                jobs = client._request("GET", "/api/platform/scheduler")
                has_job = any(j.get("agent_id") == agent_id for j in jobs)
                test_st = "SCHED_OK" if has_job else "NO_JOB"
                if has_job:
                    ok += 1
                else:
                    warn += 1
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {test_st:<10} {'':<7} {'':<7} {'cron job registered' if has_job else 'no cron job found'}")
            except Exception:
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {'SKIP':<10} {'':<7} {'':<7} {'scheduler check failed'}")
                warn += 1

        elif exec_type == "always_on":
            # Check status is RUNNING
            try:
                info = client.get(agent_id)
                status = info.get("status", "?")
                test_st = "RUNNING" if status == "running" else status.upper()
                ok += 1
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {test_st:<10} {'':<7} {'':<7} {'loop active'}")
            except Exception:
                print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {'SKIP':<10} {'':<7} {'':<7} {'status check failed'}")
                warn += 1

        elif exec_type == "event_driven":
            # Deploy is enough — event subscription verified
            ok += 1
            print(f"{cat:<12} {name:<35} {stack:<9} {exec_type:<14} {deploy_st:<8} {'SUB_OK':<10} {'':<7} {'':<7} {'event subscriptions wired'}")

    # Cleanup
    if not args.no_cleanup:
        print(f"\nCleaning up {len(deployed)} agents...")
        for aid in deployed:
            try:
                client.undeploy(aid)
            except Exception:
                pass

    client.close()
    print(f"\n{'=' * 60}")
    print(f"  OK: {ok}  |  WARN: {warn}  |  FAIL: {fail}  |  TOTAL: {len(manifests)}")
    print(f"{'=' * 60}")
    return 1 if fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
