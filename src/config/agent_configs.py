"""
Agent configuration loader — Helios OS platform layer.

This module delegates to the active company package (default: leadforge).
All symbols are re-exported for backward compatibility.

To use a different company:
    from src.config.agent_configs import load_company_module
    mod = load_company_module("dealforge")
    registry = mod.build_registry()
"""

from __future__ import annotations

import importlib
import os
import yaml
from pathlib import Path

from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier

# ---------------------------------------------------------------------------
# Default company: LeadForge AI (backward-compatible re-exports)
# ---------------------------------------------------------------------------

from src.companies.leadforge.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)


# ---------------------------------------------------------------------------
# Multi-company loader
# ---------------------------------------------------------------------------

def load_company_module(company_id: str = "leadforge"):
    """Dynamically import a company's agent_configs module."""
    return importlib.import_module(f"src.companies.{company_id}.agent_configs")


def load_company_workflows(company_id: str = "leadforge"):
    """Dynamically import a company's workflows module."""
    return importlib.import_module(f"src.companies.{company_id}.workflows")


def load_company_knowledge(company_id: str = "leadforge"):
    """Dynamically import a company's knowledge module."""
    return importlib.import_module(f"src.companies.{company_id}.knowledge")


def load_company_demo(company_id: str = "leadforge"):
    """Dynamically import a company's demo module."""
    return importlib.import_module(f"src.companies.{company_id}.demo")


# ---------------------------------------------------------------------------
# Company config YAML loader
# ---------------------------------------------------------------------------

def load_company_config(config_path: str | None = None, company_id: str = "leadforge") -> dict:
    """Load company configuration from YAML file.

    Searches in order:
    1. Explicit config_path if provided
    2. src/companies/<company_id>/config.yaml
    3. config/company-config.yaml (legacy)
    """
    if config_path:
        path = Path(config_path)
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
        return {}

    # Try company-specific config
    company_config = Path(__file__).parent.parent / "companies" / company_id / "config.yaml"
    if company_config.exists():
        with open(company_config) as f:
            return yaml.safe_load(f)

    # Fall back to legacy path
    legacy_config = Path(__file__).parent.parent.parent / "config" / "company-config.yaml"
    if legacy_config.exists():
        with open(legacy_config) as f:
            return yaml.safe_load(f)

    return {}
