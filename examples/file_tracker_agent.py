#!/usr/bin/env python3
"""
Helios OS File Tracker Agent

Deploys a local agent that counts files added to your computer
in the last 7 days. Scans ~/Documents, ~/Downloads, ~/Desktop.

Run:
    # Option 1: Standalone (no Helios OS server needed)
    PYTHONPATH=. python3 examples/file_tracker_agent.py

    # Option 2: Deploy to running Helios OS
    PYTHONPATH=. python3 examples/file_tracker_agent.py --deploy

What it does:
    1. Scans your home directories for files created in the last 7 days
    2. Groups by directory and file type
    3. Shows the largest files
    4. Optionally notifies you via A2H (if humans registered)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"
M = "\033[95m"; R = "\033[91m"; D = "\033[90m"; RS = "\033[0m"


def scan_recent_files(
    directories: list[str] | None = None,
    days: int = 7,
    max_depth: int = 3,
) -> dict:
    """Scan directories for files created in the last N days.

    This is the custom tool the agent uses. Returns structured data
    the LLM can summarize.
    """
    if not directories:
        home = Path.home()
        directories = [
            str(home / "Documents"),
            str(home / "Downloads"),
            str(home / "Desktop"),
        ]

    cutoff = time.time() - (days * 86400)
    results = {
        "period_days": days,
        "scanned_dirs": directories,
        "total_files": 0,
        "total_size_mb": 0.0,
        "by_directory": {},
        "by_extension": defaultdict(int),
        "largest_files": [],
        "newest_files": [],
    }

    all_files = []

    for base_dir in directories:
        base = Path(base_dir)
        if not base.exists():
            continue

        dir_count = 0
        dir_size = 0

        for root, dirs, files in os.walk(base):
            # Limit depth
            depth = str(root).count(os.sep) - str(base).count(os.sep)
            if depth >= max_depth:
                dirs.clear()
                continue

            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for fname in files:
                if fname.startswith('.'):
                    continue
                fpath = Path(root) / fname
                try:
                    stat = fpath.stat()
                    # Check creation time (or modification time as fallback)
                    ctime = getattr(stat, 'st_birthtime', stat.st_mtime)
                    if ctime >= cutoff:
                        size = stat.st_size
                        ext = fpath.suffix.lower() or "(no extension)"
                        dir_count += 1
                        dir_size += size
                        results["by_extension"][ext] += 1
                        all_files.append({
                            "path": str(fpath),
                            "name": fname,
                            "size": size,
                            "created": datetime.fromtimestamp(ctime).isoformat(),
                            "extension": ext,
                            "directory": base_dir,
                        })
                except (OSError, PermissionError):
                    continue

        dir_name = base.name
        results["by_directory"][dir_name] = {
            "count": dir_count,
            "size_mb": round(dir_size / 1_048_576, 1),
        }

    results["total_files"] = len(all_files)
    results["total_size_mb"] = round(sum(f["size"] for f in all_files) / 1_048_576, 1)
    results["by_extension"] = dict(sorted(
        results["by_extension"].items(), key=lambda x: -x[1]
    )[:15])

    # Top 10 largest files
    all_files.sort(key=lambda f: -f["size"])
    results["largest_files"] = [
        {"name": f["name"], "size_mb": round(f["size"] / 1_048_576, 1),
         "directory": Path(f["directory"]).name, "created": f["created"][:10]}
        for f in all_files[:10]
    ]

    # Top 10 newest files
    all_files.sort(key=lambda f: f["created"], reverse=True)
    results["newest_files"] = [
        {"name": f["name"], "size_mb": round(f["size"] / 1_048_576, 1),
         "directory": Path(f["directory"]).name, "created": f["created"][:10]}
        for f in all_files[:10]
    ]

    return results


def print_report(data: dict):
    """Pretty-print the scan results."""
    print(f"\n{B}{C}{'='*60}{RS}")
    print(f"{B}{C}  File Tracker — Last {data['period_days']} Days{RS}")
    print(f"{B}{C}{'='*60}{RS}")

    print(f"\n  {B}Summary{RS}")
    print(f"  {G}✓{RS} Total new files: {B}{data['total_files']}{RS}")
    print(f"  {G}✓{RS} Total size: {B}{data['total_size_mb']} MB{RS}")

    print(f"\n  {B}By Directory{RS}")
    for dir_name, info in data["by_directory"].items():
        bar = "█" * min(40, info["count"] // 2) + "░" * max(0, 20 - info["count"] // 2)
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


async def deploy_to_forgeos():
    """Deploy as a Helios OS agent to a running platform."""
    import httpx

    BASE = "http://localhost:5000"
    client = httpx.Client(base_url=BASE, timeout=120)

    # Check platform
    try:
        client.get("/api/platform/agents")
        print(f"{G}✓{RS} Platform online at {BASE}")
    except Exception:
        print(f"{R}✗{RS} Platform not running. Start with:")
        print(f"  PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000")
        return

    # Deploy agent
    agent_def = {
        "name": "file-tracker",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "local",
        "description": "Scans local filesystem for recently created files and reports statistics",
        "tools": ["mcp__filesystem__list_directory", "mcp__filesystem__read_file",
                   "company__record_metric", "company__publish_event"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are file-tracker, a local filesystem monitoring agent. "
            "When asked about recent files, scan the user's Documents, Downloads, and Desktop "
            "directories. Report: total count, breakdown by directory and file type, "
            "largest files, and newest files. Be concise and visual with your report."
        ),
    }

    resp = client.post("/api/platform/agents", json=agent_def)
    if resp.status_code in (200, 201):
        agent_id = resp.json().get("agent_id")
        print(f"{G}✓{RS} Deployed: file-tracker → {agent_id}")
    else:
        # Already exists
        agents = client.get("/api/platform/agents").json()
        agent_id = next((a["agent_id"] for a in agents if a["name"] == "file-tracker"), None)
        if agent_id:
            print(f"{Y}→{RS} Already deployed: {agent_id}")
        else:
            print(f"{R}✗{RS} Deploy failed: {resp.text[:200]}")
            return

    # Invoke
    print(f"{Y}→{RS} Invoking file-tracker...")
    resp = client.post(f"/api/platform/agents/{agent_id}/invoke",
        json={"prompt": "How many files have been added to my computer in the last 7 days? Check Documents, Downloads, and Desktop."})
    result = resp.json()
    print(f"{G}✓{RS} Status: {result.get('status')}")
    print(f"\n{result.get('result') or result.get('output', '')}")


def main():
    if "--deploy" in sys.argv:
        asyncio.run(deploy_to_forgeos())
    else:
        # Standalone mode — scan directly, no Helios OS needed
        print(f"{Y}→{RS} Scanning filesystem (last 7 days)...")
        data = scan_recent_files(days=7)
        print_report(data)

        # Show what Helios OS would do
        print(f"{D}  To deploy this as a Helios OS agent:{RS}")
        print(f"{D}  PYTHONPATH=. python3 examples/file_tracker_agent.py --deploy{RS}")
        print()


if __name__ == "__main__":
    main()
