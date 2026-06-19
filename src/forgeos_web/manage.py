#!/usr/bin/env python3
"""Django management entry point for the ForgeOS web layer.

Run from the repo root so the ``src`` package is importable, e.g.:
    PYTHONPATH=. python src/forgeos_web/manage.py check
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    # Repo root = parents[2] (src/forgeos_web/manage.py -> repo root).
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "src.forgeos_web.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
