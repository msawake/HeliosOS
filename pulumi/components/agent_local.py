"""Local per-agent workload (M1) — Deployment + Service, no autoscaler yet.

The local-target sibling of `agent_base.AgentWorkload`. Runs the agent-base image
as a long-running pod that the platform dispatches to over HTTP (`/invoke`), and
exposes it via a ClusterIP Service `http://<name>.<namespace>`. No Pub/Sub
Subscription, no KEDA ScaledObject (those arrive in P2). Agent identity/config is
injected as AGENT_* env (the agent-base runtime reads them), so no manifest fetch
is needed locally.
"""
from __future__ import annotations

import json

import pulumi
import pulumi_kubernetes as k8s


class LocalAgentWorkload(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        namespace: pulumi.Input[str],
        image: str,
        system_prompt: str,
        model: str,
        provider: str,
        tools: list[str],
        gemini_key: str,
        k8s_provider: k8s.Provider,
        platform_url: str = "",
        host_gateway_ip: str = "192.168.65.254",
        openai_base_url: str = "",
        openai_key: str = "",
        openai_max_tokens: str = "",
        max_turns: int = 6,
        cpu: str = "250m",
        memory: str = "512Mi",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:agent:LocalAgentWorkload", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)
        labels = {"app": name, "forgeos.io/agent": name}

        # Tools proxied back to the Platform API (which holds the Drive service
        # account + the A2A handler). drive__*/memory__*/company__* execute on the
        # platform with the SA; agent__* (A2A) are also proxied — the platform's
        # A2A handler dispatches the callee, which itself forwards to the callee's
        # own pod (P1.7), so agents collaborate pod→platform→pod. (A2H/human__*
        # still excluded — no in-cluster gateway yet.)
        # shell/fs/git/gh (dev_tools) are also proxied so a code-builder pod runs
        # them on the platform host (which has the toolchain + GH_TOKEN); the pod
        # image stays slim.
        proxied = [t for t in (tools or []) if t.split("__")[0] in (
            "drive", "memory", "company", "agent", "shell", "fs", "git", "gh")]
        wire_platform = bool(platform_url and proxied)

        env = [
            k8s.core.v1.EnvVarArgs(name="AGENT_ID", value=name),
            k8s.core.v1.EnvVarArgs(name="AGENT_NAMESPACE", value=namespace),
            k8s.core.v1.EnvVarArgs(name="AGENT_TOKEN", value="local-dev"),
            k8s.core.v1.EnvVarArgs(name="AGENT_PROVIDER", value=provider),
            k8s.core.v1.EnvVarArgs(name="AGENT_MODEL", value=model),
            k8s.core.v1.EnvVarArgs(name="AGENT_SYSTEM_PROMPT", value=system_prompt),
            k8s.core.v1.EnvVarArgs(name="AGENT_TOOLS", value=json.dumps(proxied if wire_platform else [])),
            k8s.core.v1.EnvVarArgs(name="AGENT_MAX_TURNS", value=str(max_turns)),
            k8s.core.v1.EnvVarArgs(name="GEMINI_API_KEY", value=gemini_key),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_AGENT_PORT", value="8080"),
        ]
        # OpenAI-compatible gateway (e.g. the Qwen atlas-router). The in-pod
        # SandboxRunner routes non-google/anthropic providers through _call_openai,
        # which honors OPENAI_BASE_URL / OPENAI_API_KEY — so provider=vllm + these
        # env vars point the agent's LLM at the gateway.
        if openai_base_url:
            env.append(k8s.core.v1.EnvVarArgs(name="OPENAI_BASE_URL", value=openai_base_url))
            env.append(k8s.core.v1.EnvVarArgs(name="OPENAI_API_KEY", value=openai_key or "EMPTY"))
            # Per-turn output budget. The runner defaults to 65536 (tuned for a
            # real vLLM backend), but gateways with a hard request timeout (the
            # ally-code-dev LiteLLM proxy caps at 30s) need a smaller cap so a
            # reasoning model finishes generating in time — else large-context
            # turns 408. 8192 fits these agents' outputs under 30s.
            if openai_max_tokens:
                env.append(k8s.core.v1.EnvVarArgs(name="FORGEOS_OPENAI_MAX_TOKENS", value=openai_max_tokens))
        if wire_platform:
            # Route tool calls to the host platform; register for a scoped token.
            env.append(k8s.core.v1.EnvVarArgs(name="FORGEOS_API_URL", value=platform_url))
            env.append(k8s.core.v1.EnvVarArgs(name="FORGEOS_REGISTER", value="1"))

        self.deployment = k8s.apps.v1.Deployment(
            f"{name}-deploy",
            metadata=k8s.meta.v1.ObjectMetaArgs(name=name, namespace=namespace, labels=labels),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=1,  # M1: fixed; KEDA scale-to-zero arrives in P2
                selector=k8s.meta.v1.LabelSelectorArgs(match_labels=labels),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
                    spec=k8s.core.v1.PodSpecArgs(
                        # Let pods reach the host platform (kind on Docker Desktop).
                        host_aliases=(
                            [k8s.core.v1.HostAliasArgs(ip=host_gateway_ip, hostnames=["host.docker.internal"])]
                            if wire_platform else None
                        ),
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="agent",
                                image=image,
                                image_pull_policy="IfNotPresent",  # use the kind-loaded image
                                env=env,
                                ports=[k8s.core.v1.ContainerPortArgs(name="http", container_port=8080)],
                                readiness_probe=k8s.core.v1.ProbeArgs(
                                    http_get=k8s.core.v1.HTTPGetActionArgs(path="/healthz", port=8080),
                                    initial_delay_seconds=2,
                                    period_seconds=5,
                                ),
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"cpu": cpu, "memory": memory},
                                    limits={"cpu": cpu, "memory": memory},
                                ),
                            )
                        ],
                    ),
                ),
            ),
            opts=child,
        )

        self.service = k8s.core.v1.Service(
            f"{name}-svc",
            metadata=k8s.meta.v1.ObjectMetaArgs(name=name, namespace=namespace, labels=labels),
            spec=k8s.core.v1.ServiceSpecArgs(
                selector=labels,
                ports=[k8s.core.v1.ServicePortArgs(name="http", port=80, target_port=8080)],
            ),
            opts=child,
        )

        self.register_outputs({})
