"""
MCP Registry — indexes 4,500+ MCP server packages and makes them
discoverable by the wizard agent and any deployed agent.

Data sourced from toolsdk-ai/toolsdk-mcp-registry. Each package has
a name, category, and JSON config with connection details.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MCPRegistry:
    """
    Indexes MCP server packages from resources/mcps/ and provides
    search and retrieval for the wizard and agents.
    """

    def __init__(self, registry_dir: str | Path | None = None):
        if registry_dir is None:
            registry_dir = Path(__file__).resolve().parent.parent.parent / "resources" / "mcps"
        self._dir = Path(registry_dir)
        self._packages: dict[str, dict] = {}
        self._categories: list[dict] = []
        self._indexed = False

    def index(self) -> int:
        """Load the package index. Returns count."""
        pkg_file = self._dir / "packages-list.json"
        cat_file = self._dir / "categories-list.json"

        if not pkg_file.exists():
            logger.info("MCP registry not found: %s", self._dir)
            return 0

        try:
            with open(pkg_file) as f:
                self._packages = json.load(f)
        except Exception as e:
            logger.warning("Failed to load MCP package index: %s", e)
            return 0

        try:
            with open(cat_file) as f:
                self._categories = json.load(f)
        except Exception:
            self._categories = []

        self._indexed = True
        logger.info("MCP registry indexed: %d packages, %d categories", len(self._packages), len(self._categories))
        return len(self._packages)

    def get_categories(self) -> list[dict]:
        """List all MCP categories with counts."""
        self._ensure_indexed()
        cat_counts: dict[str, int] = {}
        for info in self._packages.values():
            cat = info.get("category", "other") if isinstance(info, dict) else "other"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        return [{"category": c, "count": n} for c, n in sorted(cat_counts.items(), key=lambda x: -x[1])]

    def search(self, query: str, category: str | None = None, limit: int = 15) -> list[dict]:
        """Search MCP packages by keyword."""
        self._ensure_indexed()
        query_lower = query.lower()
        results = []

        for name, info in self._packages.items():
            if not isinstance(info, dict):
                continue
            if category and info.get("category", "") != category:
                continue

            score = 0
            name_lower = name.lower()
            desc = str(info.get("description", "")).lower()
            cat = str(info.get("category", "")).lower()

            if query_lower in name_lower:
                score += 3
            if query_lower in desc:
                score += 2
            if query_lower in cat:
                score += 1

            if score > 0:
                results.append((score, {
                    "name": name,
                    "category": info.get("category", ""),
                    "description": str(info.get("description", ""))[:150],
                    "path": info.get("path", ""),
                }))

        results.sort(key=lambda x: x[0], reverse=True)
        return [r[1] for r in results[:limit]]

    def get_package(self, name: str) -> dict | None:
        """Get full package details including connection config."""
        self._ensure_indexed()
        info = self._packages.get(name)
        if not info or not isinstance(info, dict):
            return None

        result = {
            "name": name,
            "category": info.get("category", ""),
            "description": info.get("description", ""),
        }

        # Try to load the full JSON config
        config_path = info.get("path", "")
        if config_path:
            full_path = self._dir / "packages" / config_path
            if full_path.exists():
                try:
                    with open(full_path) as f:
                        config = json.load(f)
                    result["config"] = config
                except Exception:
                    pass

        return result

    def get_by_category(self, category: str, limit: int = 20) -> list[dict]:
        """List packages in a category."""
        self._ensure_indexed()
        results = []
        for name, info in self._packages.items():
            if not isinstance(info, dict):
                continue
            if info.get("category", "") == category:
                results.append({
                    "name": name,
                    "description": str(info.get("description", ""))[:150],
                })
                if len(results) >= limit:
                    break
        return results

    def count(self) -> int:
        self._ensure_indexed()
        return len(self._packages)

    def _ensure_indexed(self):
        if not self._indexed:
            self.index()
