"""Local target — per-agent pods on a local k8s cluster (kind).

Reads the law-firm example manifests for each declared agent (so the pod gets the
agent's real system prompt / model), creates a namespace per agent, and deploys a
LocalAgentWorkload (Deployment + Service) each. No GCP, no creds beyond the Gemini
key (read from the env that runs `pulumi up`).

Config (`Pulumi.local.yaml`):
  forgeos:target: local
  forgeos:kubeContext: kind-forgeos
  forgeos:agents:
    - { name: law-firm-associate, namespace: legal, path: examples/law-firm/associate }
    - ...
"""
from __future__ import annotations

import os

import pulumi
import pulumi_kubernetes as k8s
import yaml

from components.agent_local import LocalAgentWorkload

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

config = pulumi.Config()
kube_context = config.get("kubeContext") or "kind-forgeos"
agents = config.get_object("agents") or []

gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
if not gemini_key:
    pulumi.log.warn("No GEMINI_API_KEY/GOOGLE_API_KEY in env — agent pods will start but invoke will fail.")

# Optional global LLM override — point every agent's in-pod LLM at an
# OpenAI-compatible gateway (e.g. the Qwen atlas-router) without editing each
# manifest. Set FORGEOS_AGENT_PROVIDER=vllm + FORGEOS_AGENT_MODEL=qwen3.6-27b +
# OPENAI_BASE_URL + OPENAI_API_KEY at `pulumi up`. Reversible: unset to revert.
llm_provider_override = os.environ.get("FORGEOS_AGENT_PROVIDER", "").strip()
llm_model_override = os.environ.get("FORGEOS_AGENT_MODEL", "").strip()
openai_base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
# Per-turn output cap for the OpenAI/vLLM path. Defaults to 8192 so reasoning
# models (Qwen 3.6) finish within the ally-code-dev gateway's hard 30s request
# timeout; override via FORGEOS_OPENAI_MAX_TOKENS for a backend without that cap.
openai_max_tokens = os.environ.get("FORGEOS_OPENAI_MAX_TOKENS", "8192").strip()

provider = k8s.Provider("local-k8s", context=kube_context)


def _load_agent_cfg(path: str) -> dict:
    """Read an example agent's manifest + its system_prompt file."""
    base = os.path.join(_REPO, path)
    man = yaml.safe_load(open(os.path.join(base, "manifest.yaml")))
    spec = man["spec"]
    sp = spec.get("system_prompt", {})
    if isinstance(sp, dict) and sp.get("file"):
        system_prompt = open(os.path.join(base, sp["file"])).read()
    elif isinstance(sp, dict):
        system_prompt = sp.get("content", "")
    else:
        system_prompt = str(sp)
    llm = spec.get("llm", {})
    return {
        "system_prompt": system_prompt,
        "model": llm.get("chat_model", "gemini-2.5-pro"),
        "provider": llm.get("provider", "google"),
        "tools": spec.get("tools", []),
        "max_turns": int(spec.get("metadata", {}).get("max_turns", 6)),
    }


# One namespace per distinct agent namespace.
namespaces = sorted({a["namespace"] for a in agents})
ns_res: dict[str, k8s.core.v1.Namespace] = {}
for ns in namespaces:
    ns_res[ns] = k8s.core.v1.Namespace(
        f"ns-{ns}",
        metadata=k8s.meta.v1.ObjectMetaArgs(name=ns, labels={"forgeos.io/namespace": ns}),
        opts=pulumi.ResourceOptions(provider=provider),
    )

workloads = {}
for spec in agents:
    cfg = _load_agent_cfg(spec["path"])
    workloads[spec["name"]] = LocalAgentWorkload(
        name=spec["name"],
        namespace=spec["namespace"],
        image=config.get("agentImage") or "forgeos/agent-base:dev",
        system_prompt=cfg["system_prompt"],
        model=llm_model_override or cfg["model"],
        provider=llm_provider_override or cfg["provider"],
        tools=cfg["tools"],
        gemini_key=gemini_key,
        k8s_provider=provider,
        platform_url=config.get("platformUrl") or "http://host.docker.internal:5000",
        host_gateway_ip=config.get("hostGatewayIp") or "192.168.65.254",
        openai_base_url=openai_base_url,
        openai_key=openai_key,
        openai_max_tokens=openai_max_tokens,
        max_turns=cfg["max_turns"],
        opts=pulumi.ResourceOptions(depends_on=[ns_res[spec["namespace"]]]),
    )

# ---------------------------------------------------------------------------
# P2 — KEDA autoscale (scale-to-zero). Local proof of the per-agent autoscaler.
# A trigger backlog (a Redis list `agent:<name>`) drives KEDA to scale each agent
# Deployment 0..N. On GCP the same ScaledObject is emitted with a Pub/Sub-backed
# metrics-api trigger instead (target-aware) — the scale-to-zero behavior is what
# we prove here. Toggle with `forgeos-gcp:enableAutoscale`.
# ---------------------------------------------------------------------------
if config.get_bool("enableAutoscale"):
    data_ns = k8s.core.v1.Namespace(
        "ns-forgeos-data",
        metadata=k8s.meta.v1.ObjectMetaArgs(name="forgeos-data"),
        opts=pulumi.ResourceOptions(provider=provider),
    )
    redis_labels = {"app": "forgeos-redis"}
    k8s.apps.v1.Deployment(
        "redis-deploy",
        metadata=k8s.meta.v1.ObjectMetaArgs(name="redis", namespace="forgeos-data", labels=redis_labels),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(match_labels=redis_labels),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(labels=redis_labels),
                spec=k8s.core.v1.PodSpecArgs(containers=[
                    k8s.core.v1.ContainerArgs(
                        name="redis", image="redis:7-alpine",
                        ports=[k8s.core.v1.ContainerPortArgs(container_port=6379)],
                    )
                ]),
            ),
        ),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[data_ns]),
    )
    redis_svc = k8s.core.v1.Service(
        "redis-svc",
        metadata=k8s.meta.v1.ObjectMetaArgs(name="redis", namespace="forgeos-data", labels=redis_labels),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector=redis_labels,
            ports=[k8s.core.v1.ServicePortArgs(port=6379, target_port=6379)],
        ),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[data_ns]),
    )
    redis_addr = "redis.forgeos-data.svc.cluster.local:6379"
    # pinReplicas=true → exactly 1 pod per agent (min=max=1), no autoscaling — a
    # clean steady-state view. Otherwise scale-to-zero (min=0) up to max_replicas.
    pin = config.get_bool("pinReplicas")
    for spec in agents:
        name, ns = spec["name"], spec["namespace"]
        always_on = bool(spec.get("always_on", False))
        min_replicas = 1 if (pin or always_on) else 0
        max_replicas = 1 if pin else int(spec.get("max_replicas", 3))
        k8s.apiextensions.CustomResource(
            f"{name}-scaler",
            api_version="keda.sh/v1alpha1",
            kind="ScaledObject",
            metadata=k8s.meta.v1.ObjectMetaArgs(name=f"{name}-scaler", namespace=ns),
            spec={
                "scaleTargetRef": {"name": name},
                "minReplicaCount": min_replicas,
                "maxReplicaCount": max_replicas,
                "pollingInterval": 5,
                "cooldownPeriod": 15,
                "triggers": [{
                    "type": "redis",
                    "metadata": {
                        "address": redis_addr,
                        "listName": f"agent:{name}",
                        "listLength": "1",
                        "enableTLS": "false",
                    },
                }],
            },
            opts=pulumi.ResourceOptions(provider=provider, depends_on=[workloads[name].deployment, redis_svc]),
        )

pulumi.export("target", "local")
pulumi.export("agents", [a["name"] for a in agents])
pulumi.export("namespaces", namespaces)
