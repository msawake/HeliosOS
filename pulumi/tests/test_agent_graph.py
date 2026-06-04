"""Pulumi mock tests for the per-agent resource graph (no infra, runs in CI).

Uses `pulumi.runtime.set_mocks` with a recording mock to assert the shape of the
graph each `AgentWorkload` produces: one Pub/Sub Subscription + one k8s
Deployment + one KEDA ScaledObject per agent, the correct per-agent subscription
filter, and replicas reflecting `always_on`.

All `@pulumi.runtime.test` functions share one runtime + one global RESOURCES
list, so tests must NOT clear it (clears race async registrations). Instead each
test uses unique agent names and filters by name — making assertions
order-independent and contamination-free.

Run: `pulumi/venv/bin/python -m pytest pulumi/tests -q`
"""
from __future__ import annotations

import os
import sys

import pulumi

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESOURCES: list[tuple[str, str, dict]] = []


class _RecordingMocks(pulumi.runtime.Mocks):
    def new_resource(self, args: pulumi.runtime.MockResourceArgs):
        RESOURCES.append((args.typ, args.name, dict(args.inputs)))
        return [args.name + "_id", dict(args.inputs)]

    def call(self, args: pulumi.runtime.MockCallArgs):
        return {}


pulumi.runtime.set_mocks(_RecordingMocks(), project="forgeos", stack="test", preview=False)

import pulumi_kubernetes as k8s  # noqa: E402
from components.agent_base import AgentWorkload  # noqa: E402

_PROVIDER = k8s.Provider("kprov", kubeconfig="{}")


def _make_agent(name: str, always_on: bool = False) -> AgentWorkload:
    return AgentWorkload(
        name=name,
        namespace="legal",
        image="reg/agent-base:dev",
        manifest_ref="gs://x/" + name + ".yaml",
        pubsub_topic="forgeos-agent-triggers",
        project="forgeos",
        k8s_provider=_PROVIDER,
        platform_api_url="http://platform-api",
        always_on=always_on,
        max_replicas=7,
    )


def _for(names: list[str]) -> list[tuple[str, str, dict]]:
    return [r for r in RESOURCES if any(r[1].startswith(n) for n in names)]


def _kind(rs, suffix):
    return [r for r in rs if r[0].endswith(suffix)]


def _scaled(rs):
    return [r for r in rs if "ScaledObject" in r[0]]


@pulumi.runtime.test
def test_one_agent_emits_deployment_subscription_scaledobject():
    aw = _make_agent("g1-associate", always_on=False)

    def check(_):
        rs = _for(["g1-associate"])
        assert len(_kind(rs, ":Deployment")) == 1, [r[0] for r in rs]
        assert len(_kind(rs, ":Subscription")) == 1, [r[0] for r in rs]
        assert len(_scaled(rs)) == 1, [r[0] for r in rs]

    return aw.scaledobject.urn.apply(check)


@pulumi.runtime.test
def test_subscription_filters_per_agent():
    aw = _make_agent("g2-conflicts")

    def check(_):
        sub = _kind(_for(["g2-conflicts"]), ":Subscription")[0][2]
        assert sub.get("filter") == 'attributes.agent = "g2-conflicts"', sub
        assert sub.get("topic") == "forgeos-agent-triggers", sub

    return aw.subscription.id.apply(check)


@pulumi.runtime.test
def test_replicas_reflect_always_on():
    aws = [
        _make_agent("g3-dock", always_on=False),
        _make_agent("g3-risk", always_on=True),
    ]

    def check(_):
        deps = _kind(_for(["g3-dock", "g3-risk"]), ":Deployment")
        replicas = sorted((d[2].get("spec", {}) or {}).get("replicas", -1) for d in deps)
        assert replicas == [0, 1], [(d[1], (d[2].get("spec", {}) or {}).get("replicas")) for d in deps]

    return pulumi.Output.all(*[a.deployment.urn for a in aws]).apply(check)


@pulumi.runtime.test
def test_n_agents_yield_n_of_each():
    names = ["g4-a", "g4-b", "g4-c", "g4-d"]
    aws = [_make_agent(n) for n in names]

    def check(_):
        rs = _for(names)
        assert len(_kind(rs, ":Deployment")) == len(names), [r[0] for r in rs]
        assert len(_kind(rs, ":Subscription")) == len(names)
        assert len(_scaled(rs)) == len(names)

    return pulumi.Output.all(*[a.scaledobject.urn for a in aws]).apply(check)
