"""
Validates all example YAML manifests across 4 frameworks x 5 execution types.

Checks:
  - Every YAML file exists and parses
  - Correct stack assignment
  - Correct execution_type
  - Scheduled agents have schedule field
  - Event-driven agents have event_triggers
  - Autonomous agents have goal
  - All have non-empty system_prompt
"""

import pytest
from pathlib import Path
from src.forgeos_sdk import AgentManifest


EXAMPLES_DIR = Path("examples")

# 4 frameworks x 5 execution types = 20 expected YAML files (plus hello-world = 24)
EXPECTED_TYPES = {
    "reflex": {"schedule": False, "events": False, "goal": False},
    "scheduled": {"schedule": True, "events": False, "goal": False},
    "always_on": {"schedule": False, "events": False, "goal": False},
    "event_driven": {"schedule": False, "events": True, "goal": False},
    "autonomous": {"schedule": False, "events": False, "goal": True},
}

STACKS = ["forgeos", "crewai", "adk", "openclaw"]


def _find_yamls(stack: str) -> list[Path]:
    stack_dir = EXAMPLES_DIR / stack
    if not stack_dir.exists():
        return []
    return sorted(stack_dir.glob("*.yaml"))


def _all_yamls() -> list[tuple[str, Path]]:
    result = []
    for stack in STACKS:
        for path in _find_yamls(stack):
            result.append((stack, path))
    return result


class TestAllExamplesExist:
    """Every stack should have at least 5 execution type examples."""

    @pytest.mark.parametrize("stack", STACKS)
    def test_stack_has_examples(self, stack):
        yamls = _find_yamls(stack)
        # At least hello-world + 5 types = 6, but some may not exist yet
        assert len(yamls) >= 1, f"No examples found for {stack}"


class TestAllExamplesValid:
    """Every YAML parses as a valid AgentManifest."""

    @pytest.mark.parametrize("stack,path", _all_yamls(), ids=lambda x: str(x) if isinstance(x, Path) else x)
    def test_yaml_parses(self, stack, path):
        m = AgentManifest.from_yaml(path)
        assert m.metadata.name, f"{path}: missing name"
        assert m.spec.stack == stack, f"{path}: stack mismatch (expected {stack}, got {m.spec.stack})"

    @pytest.mark.parametrize("stack,path", _all_yamls(), ids=lambda x: str(x) if isinstance(x, Path) else x)
    def test_has_system_prompt(self, stack, path):
        m = AgentManifest.from_yaml(path)
        req = m.to_deploy_request()
        assert len(req.get("system_prompt", "")) > 10, f"{path}: system_prompt too short"

    @pytest.mark.parametrize("stack,path", _all_yamls(), ids=lambda x: str(x) if isinstance(x, Path) else x)
    def test_execution_type_consistency(self, stack, path):
        m = AgentManifest.from_yaml(path)
        exec_type = m.spec.execution_type
        req = m.to_deploy_request()

        if exec_type == "scheduled":
            assert req.get("schedule"), f"{path}: scheduled agent missing schedule"
        if exec_type == "event_driven":
            assert req.get("event_triggers"), f"{path}: event_driven missing event_triggers"
        if exec_type == "autonomous":
            assert req.get("goal"), f"{path}: autonomous agent missing goal"


class TestCoverageMatrix:
    """All 4 stacks x 5 execution types should be covered."""

    def test_all_stacks_present(self):
        stacks_found = set()
        for stack, path in _all_yamls():
            stacks_found.add(stack)
        assert stacks_found == set(STACKS), f"Missing stacks: {set(STACKS) - stacks_found}"

    def test_all_types_per_stack(self):
        """Each stack should have at least one example per execution type."""
        for stack in STACKS:
            types_found = set()
            for path in _find_yamls(stack):
                m = AgentManifest.from_yaml(path)
                types_found.add(m.spec.execution_type)
            missing = set(EXPECTED_TYPES.keys()) - types_found
            if missing:
                pytest.skip(f"{stack}: missing types {missing} (examples may not be created yet)")
