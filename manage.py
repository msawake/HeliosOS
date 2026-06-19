#!/usr/bin/env python3
"""Django management entry point (root of the repo).

The Django project lives in ``forgeos_web/``; the platform library it builds on
lives in ``src/``. Run from the repo root, e.g.:
    python manage.py check
    python manage.py migrate
    python manage.py createsuperuser
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgeos_web.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
