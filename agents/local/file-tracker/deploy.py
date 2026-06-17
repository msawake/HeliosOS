#!/usr/bin/env python3
"""
Deploy and run the file-tracker agent.

    # Standalone (no Helios OS needed)
    python agents/local/file-tracker/deploy.py

    # Deploy to running Helios OS
    python agents/local/file-tracker/deploy.py --deploy

    # Deploy + invoke with custom prompt
    python agents/local/file-tracker/deploy.py --deploy --prompt "Scan my Downloads folder only"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"
M = "\033[95m"; R = "\033[91m"; D = "\033[90m"; RS = "\033[0m"


def print_report(data: dict):
    """Pretty-print scan results."""
    print(f"\n{B}{C}{'='*60}{RS}")
    print(f"{B}{C}  File Tracker — Last {data['period_days']} Days{RS}")
    print(f"{B}{C}{'='*60}{RS}")

    print(f"\n  {B}Summary{RS}")
    print(f"  {G}✓{RS} Total new files: {B}{data['total_files']}{RS}")
    print(f"  {G}✓{RS} Total size: {B}{data['total_size_mb']} MB{RS}")

    print(f"\n  {B}By Directory{RS}")
    for dir_name, info in data["by_directory"].items():
        if info.get("error"):
            print(f"  {R}{dir_name:15}{RS} {info['error']}")
            continue
        bar_len = min(40, max(1, info["count"] // 2))
        bar = "█" * bar_len
        print(f"  {Y}{dir_name:15}{RS} {info['count']:4} files  {D}({info['size_mb']} MB){RS}  {C}{bar}{RS}")

    print(f"\n  {B}By File Type (top 10){RS}")
    for ext, count in list(data["by_extension"].items())[:10]:
        bar = "█" * min(30, count)
        print(f"  {M}{ext:15}{RS} {count:4}  {C}{bar}{RS}")

    if data["largest_files"]:
        print(f"\n  {B}Largest Files{RS}")
        for f in data["largest_files"][:5]:
            print(f"  {R}{f['size_mb']:7.1f} MB{RS}  {f['name'][:40]:40}  {D}{f['directory']}/{RS}")

    if data["newest_files"]:
        print(f"\n  {B}Newest Files{RS}")
        for f in data["newest_files"][:5]:
            print(f"  {G}{f['created']}{RS}  {f['name'][:40]:40}  {D}{f['directory']}/{RS}")

    print()


async def deploy_to_forgeos(prompt: str | None = None):
    """Deploy as a Helios OS agent."""
    import httpx

    BASE = "http://localhost:5000"
    client = httpx.Client(base_url=BASE, timeout=120)

    try:
        client.get("/api/platform/agents")
        print(f"{G}✓{RS} Platform online")
    except Exception:
        print(f"{R}✗{RS} Platform not running. Start with:")
        print(f"  PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000")
        return

    # Read system prompt from file
    prompt_file = Path(__file__).parent / "prompts" / "system.md"
    system_prompt = prompt_file.read_text() if prompt_file.exists() else ""

    agent_def = {
        "name": "file-tracker",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "local",
        "description": "Scans local filesystem for recently created files",
        "tools": ["file_tracker__scan_recent", "file_tracker__scan_directory",
                   "company__record_metric", "human__notify"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": system_prompt,
    }

    resp = client.post("/api/platform/agents", json=agent_def)
    if resp.status_code in (200, 201):
        agent_id = resp.json().get("agent_id")
        print(f"{G}✓{RS} Deployed: file-tracker → {agent_id}")
    else:
        agents = client.get("/api/platform/agents").json()
        agent_id = next((a["agent_id"] for a in agents if a["name"] == "file-tracker"), None)
        if agent_id:
            print(f"{Y}→{RS} Already deployed: {agent_id}")
        else:
            print(f"{R}✗{RS} Deploy failed: {resp.text[:200]}")
            return

    user_prompt = prompt or "How many files have been added to my computer in the last 7 days?"
    print(f"{Y}→{RS} Invoking: {user_prompt}")

    resp = client.post(f"/api/platform/agents/{agent_id}/invoke",
                       json={"prompt": user_prompt})
    result = resp.json()
    print(f"{G}✓{RS} Status: {result.get('status')}")
    print(f"\n{result.get('result') or result.get('output', '')}")


def main():
    if "--deploy" in sys.argv:
        prompt = None
        if "--prompt" in sys.argv:
            idx = sys.argv.index("--prompt")
            if idx + 1 < len(sys.argv):
                prompt = sys.argv[idx + 1]
        asyncio.run(deploy_to_forgeos(prompt))
    else:
        from tools import scan_recent_files
        print(f"{Y}→{RS} Scanning filesystem (last 7 days)...")
        data = scan_recent_files(days=7)
        print_report(data)
        print(f"{D}  Deploy to Helios OS: python agents/local/file-tracker/deploy.py --deploy{RS}\n")


if __name__ == "__main__":
    main()
