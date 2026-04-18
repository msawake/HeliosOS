"""Tests for the hello-world example agents.

Validates that each example's Agent class compiles to a valid AgentManifest
and produces a correct deploy request. Does NOT require a running backend.
"""

import pytest
from pathlib import Path

from src.forgeos_sdk import Agent, AgentManifest


class TestForgeOSExample:
    def test_manifest_valid(self):
        from examples.forgeos.hello_world import HelloForgeOS
        m = HelloForgeOS.manifest()
        assert m.metadata.name == "hello-forgeos"
        assert m.spec.stack == "forgeos"
        assert m.spec.execution_type == "reflex"

    def test_deploy_request(self):
        from examples.forgeos.hello_world import HelloForgeOS
        req = HelloForgeOS.manifest().to_deploy_request()
        assert req["name"] == "hello-forgeos"
        assert req["stack"] == "forgeos"
        assert req["execution_type"] == "reflex"
        assert len(req["system_prompt"]) > 10

    def test_yaml_loads(self):
        m = AgentManifest.from_yaml("examples/forgeos/hello-world.yaml")
        assert m.metadata.name == "hello-forgeos"
        assert m.spec.stack == "forgeos"


class TestCrewAIExample:
    def test_manifest_valid(self):
        from examples.crewai.hello_world import HelloCrewAI
        m = HelloCrewAI.manifest()
        assert m.metadata.name == "hello-crewai"
        assert m.spec.stack == "crewai"

    def test_deploy_request(self):
        from examples.crewai.hello_world import HelloCrewAI
        req = HelloCrewAI.manifest().to_deploy_request()
        assert req["stack"] == "crewai"

    def test_yaml_loads(self):
        m = AgentManifest.from_yaml("examples/crewai/hello-world.yaml")
        assert m.metadata.name == "hello-crewai"


class TestADKExample:
    def test_manifest_valid(self):
        from examples.adk.hello_world import HelloADK
        m = HelloADK.manifest()
        assert m.metadata.name == "hello-adk"
        assert m.spec.stack == "adk"

    def test_yaml_loads(self):
        m = AgentManifest.from_yaml("examples/adk/hello-world.yaml")
        assert m.spec.stack == "adk"


class TestOpenClawExample:
    def test_manifest_valid(self):
        from examples.openclaw.hello_world import HelloOpenClaw
        m = HelloOpenClaw.manifest()
        assert m.metadata.name == "hello-openclaw"
        assert m.spec.stack == "openclaw"

    def test_yaml_loads(self):
        m = AgentManifest.from_yaml("examples/openclaw/hello-world.yaml")
        assert m.spec.stack == "openclaw"


class TestAllExamplesConsistency:
    """Cross-cutting tests across all 4 examples."""

    YAML_FILES = [
        "examples/forgeos/hello-world.yaml",
        "examples/crewai/hello-world.yaml",
        "examples/adk/hello-world.yaml",
        "examples/openclaw/hello-world.yaml",
    ]

    def test_all_yaml_files_exist(self):
        for path in self.YAML_FILES:
            assert Path(path).exists(), f"Missing: {path}"

    def test_all_yaml_files_valid(self):
        for path in self.YAML_FILES:
            m = AgentManifest.from_yaml(path)
            assert m.metadata.name.startswith("hello-")
            assert m.spec.execution_type == "reflex"

    def test_all_have_system_prompt(self):
        for path in self.YAML_FILES:
            m = AgentManifest.from_yaml(path)
            req = m.to_deploy_request()
            assert len(req["system_prompt"]) > 20, f"{path}: system_prompt too short"

    def test_all_stacks_covered(self):
        stacks = set()
        for path in self.YAML_FILES:
            m = AgentManifest.from_yaml(path)
            stacks.add(m.spec.stack)
        assert stacks == {"forgeos", "crewai", "adk", "openclaw"}
