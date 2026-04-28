"""
File Tracker — Custom tools.

Two tools the agent can call:
  - file_tracker__scan_recent: scan multiple directories for files created in N days
  - file_tracker__scan_directory: scan a single directory with depth control
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def scan_recent_files(
    directories: list[str] | None = None,
    days: int = 7,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Scan directories for files created in the last N days.

    Returns structured data: total count, size, breakdown by directory
    and file type, largest and newest files.
    """
    if not directories:
        home = Path.home()
        directories = [
            str(home / "Documents"),
            str(home / "Downloads"),
            str(home / "Desktop"),
        ]

    cutoff = time.time() - (days * 86400)
    all_files: list[dict] = []
    by_directory: dict[str, dict] = {}
    by_extension: dict[str, int] = defaultdict(int)

    for base_dir in directories:
        base = Path(base_dir)
        if not base.exists():
            by_directory[base.name] = {"count": 0, "size_mb": 0, "error": "directory not found"}
            continue

        dir_count = 0
        dir_size = 0

        for root, dirs, files in os.walk(base):
            depth = str(root).count(os.sep) - str(base).count(os.sep)
            if depth >= max_depth:
                dirs.clear()
                continue
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for fname in files:
                if fname.startswith('.'):
                    continue
                fpath = Path(root) / fname
                try:
                    stat = fpath.stat()
                    ctime = getattr(stat, 'st_birthtime', stat.st_mtime)
                    if ctime >= cutoff:
                        size = stat.st_size
                        ext = fpath.suffix.lower() or "(no extension)"
                        dir_count += 1
                        dir_size += size
                        by_extension[ext] += 1
                        all_files.append({
                            "path": str(fpath),
                            "name": fname,
                            "size": size,
                            "created": datetime.fromtimestamp(ctime).isoformat(),
                            "extension": ext,
                            "directory": base.name,
                        })
                except (OSError, PermissionError):
                    continue

        by_directory[base.name] = {
            "count": dir_count,
            "size_mb": round(dir_size / 1_048_576, 1),
        }

    all_files.sort(key=lambda f: -f["size"])
    largest = [
        {"name": f["name"], "size_mb": round(f["size"] / 1_048_576, 1),
         "directory": f["directory"], "created": f["created"][:10]}
        for f in all_files[:10]
    ]

    all_files.sort(key=lambda f: f["created"], reverse=True)
    newest = [
        {"name": f["name"], "size_mb": round(f["size"] / 1_048_576, 1),
         "directory": f["directory"], "created": f["created"][:10]}
        for f in all_files[:10]
    ]

    return {
        "period_days": days,
        "scanned_dirs": directories,
        "total_files": len(all_files),
        "total_size_mb": round(sum(f["size"] for f in all_files) / 1_048_576, 1),
        "by_directory": by_directory,
        "by_extension": dict(sorted(by_extension.items(), key=lambda x: -x[1])[:15]),
        "largest_files": largest,
        "newest_files": newest,
    }


def scan_directory(
    path: str,
    days: int = 7,
    max_depth: int = 2,
) -> dict[str, Any]:
    """Scan a single directory for recent files.

    Lighter than scan_recent_files — focuses on one directory.
    """
    return scan_recent_files(directories=[path], days=days, max_depth=max_depth)


# ---------------------------------------------------------------------------
# Tool schemas (for LLM tool-use)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "file_tracker__scan_recent",
        "description": (
            "Scan local directories for files created in the last N days. "
            "Returns: total count, size, breakdown by directory and file type, "
            "largest and newest files. Defaults to ~/Documents, ~/Downloads, ~/Desktop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "directories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Directories to scan (default: Documents, Downloads, Desktop)",
                },
                "days": {
                    "type": "integer",
                    "description": "How many days back to look (default: 7)",
                    "default": 7,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum directory depth to scan (default: 3)",
                    "default": 3,
                },
            },
        },
    },
    {
        "name": "file_tracker__scan_directory",
        "description": "Scan a single directory for files created in the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to scan"},
                "days": {"type": "integer", "default": 7},
                "max_depth": {"type": "integer", "default": 2},
            },
            "required": ["path"],
        },
    },
]
