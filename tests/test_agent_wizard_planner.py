"""Tests for src/platform/agent_wizard_planner.py."""

import pytest

from src.platform.agent_wizard_planner import (
    extract_json_object,
    heuristic_proposal,
    normalize_proposal,
    slugify_name,
)


def test_slugify_name():
    assert slugify_name("My Cool Agent!") == "my-cool-agent"
    assert slugify_name("   ") == "new-agent"


def test_extract_json_object_raw():
    d = extract_json_object('{"assistant_message":"hi","ready_to_deploy":false}')
    assert d is not None
    assert d["assistant_message"] == "hi"


def test_extract_json_object_fenced():
    raw = '```json\n{"a":1}\n```'
    d = extract_json_object(raw)
    assert d == {"a": 1}


def test_normalize_enterprise_maps_shared():
    p, warns = normalize_proposal(
        {
            "name": "Test Bot",
            "stack": "forgeos",
            "execution_type": "reflex",
            "ownership": "enterprise",
        }
    )
    assert p is not None
    assert p["ownership"] == "shared"
    assert any("enterprise" in w.lower() or "team" in w.lower() for w in warns)


def test_normalize_event_driven_adds_trigger():
    p, _ = normalize_proposal(
        {
            "name": "evt",
            "stack": "forgeos",
            "execution_type": "event_driven",
            "ownership": "shared",
        }
    )
    assert p is not None
    assert len(p["event_triggers"]) >= 1


def test_normalize_invalid_stack_defaults():
    p, warns = normalize_proposal(
        {
            "name": "x",
            "stack": "not-a-stack",
            "execution_type": "reflex",
            "ownership": "shared",
        }
    )
    assert p is not None
    assert p["stack"] == "forgeos"
    assert warns


def test_heuristic_keyword_scheduled():
    r = heuristic_proposal(
        [{"role": "user", "content": "Run a daily sales report for the team"}],
        context={},
    )
    assert r["_mode"] == "heuristic"
    assert r["proposal"] is not None
    assert r["proposal"]["execution_type"] == "scheduled"


def test_heuristic_personal_inbox():
    r = heuristic_proposal(
        [{"role": "user", "content": "Personal agent for my inbox when email arrives"}],
        context={},
    )
    assert r["proposal"]["ownership"] == "personal"
    assert r["proposal"].get("owner_id")


def test_heuristic_crewai_cues():
    r = heuristic_proposal(
        [{"role": "user", "content": "Multi-agent crew for marketing copy and review"}],
        context={},
    )
    assert r["proposal"]["stack"] == "crewai"
