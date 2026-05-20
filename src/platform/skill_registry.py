"""
Skill Registry — indexes .claude/skills/ SKILL.md files and makes them
discoverable by the wizard agent and any deployed agent.

Skills are markdown files with YAML frontmatter containing domain knowledge,
procedures, frameworks, and best practices. They are shared resources.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


@dataclass
class Skill:
    name: str
    description: str
    domain: str
    path: str
    content: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "path": self.path,
            "tags": self.tags,
        }

    def to_summary(self) -> dict:
        """Short summary for listing (no content)."""
        return {
            "name": self.name,
            "description": self.description[:150],
            "domain": self.domain,
        }


class SkillRegistry:
    """
    Indexes SKILL.md files from .claude/skills/ and provides
    search and retrieval for the wizard and agents.
    """

    def __init__(self, skills_dir: str | Path | None = None):
        if skills_dir is None:
            # Default: .claude/skills/ relative to repo root
            skills_dir = Path(__file__).resolve().parent.parent.parent / "resources" / "skills"
        self._dir = Path(skills_dir)
        self._skills: dict[str, Skill] = {}
        self._indexed = False

    def index(self) -> int:
        """Scan the skills directory and build the index. Returns count."""
        if not self._dir.exists():
            logger.info("Skills directory not found: %s", self._dir)
            return 0

        count = 0
        for skill_md in self._dir.rglob("SKILL.md"):
            skill = self._parse_skill(skill_md)
            if skill:
                self._skills[skill.name] = skill
                count += 1

        self._indexed = True
        logger.info("Indexed %d skills from %s", count, self._dir)
        return count

    def list_all(self) -> list[dict]:
        """List all skills (summaries only)."""
        self._ensure_indexed()
        return [s.to_summary() for s in sorted(self._skills.values(), key=lambda s: s.domain)]

    def list_by_domain(self, domain: str) -> list[dict]:
        """List skills in a specific domain."""
        self._ensure_indexed()
        return [
            s.to_summary() for s in self._skills.values()
            if s.domain == domain
        ]

    def get_domains(self) -> list[dict]:
        """List all domains with skill counts."""
        self._ensure_indexed()
        domains: dict[str, int] = {}
        for s in self._skills.values():
            domains[s.domain] = domains.get(s.domain, 0) + 1
        return [{"domain": d, "count": c} for d, c in sorted(domains.items())]

    def search(self, query: str, domain: str | None = None, limit: int = 10) -> list[dict]:
        """Search skills by keyword in name, description, and content."""
        self._ensure_indexed()
        query_lower = query.lower()
        results = []
        for s in self._skills.values():
            if domain and s.domain != domain:
                continue
            # Score: name match = 3, description match = 2, content match = 1, tag match = 2
            score = 0
            if query_lower in s.name.lower():
                score += 3
            if query_lower in s.description.lower():
                score += 2
            if query_lower in s.content.lower():
                score += 1
            if any(query_lower in t.lower() for t in s.tags):
                score += 2
            if score > 0:
                results.append((score, s))
        results.sort(key=lambda x: x[0], reverse=True)
        return [s.to_dict() for _, s in results[:limit]]

    def get(self, name: str) -> dict | None:
        """Get a skill by name, including full content."""
        self._ensure_indexed()
        skill = self._skills.get(name)
        if skill:
            result = skill.to_dict()
            result["content"] = skill.content
            return result
        return None

    def count(self) -> int:
        self._ensure_indexed()
        return len(self._skills)

    def _ensure_indexed(self):
        if not self._indexed:
            self.index()

    def _parse_skill(self, path: Path) -> Skill | None:
        """Parse a SKILL.md file into a Skill object."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        # Extract YAML frontmatter
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        meta = {}
        if HAS_YAML:
            try:
                meta = yaml.safe_load(match.group(1)) or {}
            except Exception:
                return None
        else:
            # Basic parsing without yaml
            for line in match.group(1).split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    meta[key.strip().strip('"')] = val.strip().strip('"')

        name = meta.get("name", "")
        if not name:
            # Fall back to directory name
            name = path.parent.name

        description = meta.get("description", "")

        # Domain = first directory under skills/
        try:
            rel = path.relative_to(self._dir)
            domain = rel.parts[0] if len(rel.parts) > 1 else "general"
        except ValueError:
            domain = "general"

        # Body content (after frontmatter)
        body = content[match.end():].strip()

        # Extract tags from content
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        if not isinstance(tags, list):
            tags = []

        return Skill(
            name=name,
            description=description,
            domain=domain,
            path=str(path),
            content=body[:5000],  # Cap content to avoid huge memory
            tags=tags,
        )
