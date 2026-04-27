"""
K8s Sandbox Adapter — spawns agents as Kubernetes pods.

Supports two modes:
1. Environment mode: agents are deployed into shared Environment pods
   via the in-pod manager HTTP API.
2. Legacy mode: each agent gets its own pod/Deployment (backward compat).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Any

import httpx

from stacks.base import (
    AgentDefinition, AgentResult, AgentStackAdapter, AgentStatus, build_agent_context,
)
from stacks.sandbox.adapter import SandboxTokenStore, get_token_store
from src.platform.environment import EnvironmentDefinition, EnvironmentRegistry, EnvironmentStatus

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = os.environ.get("FORGEOS_SANDBOX_IMAGE", "forgeos-sandbox:latest")
SANDBOX_NAMESPACE = os.environ.get("FORGEOS_SANDBOX_NAMESPACE", "forgeos")
SANDBOX_MEM = os.environ.get("FORGEOS_SANDBOX_MEM_LIMIT", "512Mi")
SANDBOX_CPU = os.environ.get("FORGEOS_SANDBOX_CPU_LIMIT", "500m")
SANDBOX_CPU_REQUEST = os.environ.get("FORGEOS_SANDBOX_CPU_REQUEST", "250m")
SANDBOX_MEM_REQUEST = os.environ.get("FORGEOS_SANDBOX_MEM_REQUEST", "512Mi")
MANAGER_PORT = 8080

try:
    from kubernetes import client as k8s_client, config as k8s_config, watch as k8s_watch
    HAS_K8S = True
except ImportError:
    HAS_K8S = False


class K8sSandboxAdapter(AgentStackAdapter):
    """Spawns agents as Kubernetes pods/deployments, with optional Environment grouping."""

    stack_name = "sandbox"

    def __init__(self, llm_router=None, tool_executor=None, api_url: str = "http://forgeos-api.forgeos.svc.cluster.local:5000"):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._api_url = api_url
        self._agents: dict[str, AgentDefinition] = {}
        self._tokens = get_token_store()
        self._v1: Any = None
        self._apps_v1: Any = None
        self._k8s_available = False
        self._activity_log: dict[str, deque] = {}
        self._log_cache: dict[str, str] = {}
        self._pod_names: dict[str, str] = {}
        self._env_registry = EnvironmentRegistry()
        self._http = httpx.AsyncClient(timeout=30)

        if HAS_K8S:
            try:
                k8s_config.load_incluster_config()
                logger.info("K8s sandbox adapter: in-cluster config loaded")
            except Exception:
                try:
                    k8s_config.load_kube_config()
                    logger.info("K8s sandbox adapter: kubeconfig loaded")
                except Exception as e:
                    logger.warning("K8s sandbox adapter: no config available (%s)", e)
                    return

            try:
                self._v1 = k8s_client.CoreV1Api()
                self._apps_v1 = k8s_client.AppsV1Api()
                self._k8s_available = True
                logger.info("K8s sandbox adapter: connected")
            except Exception as e:
                logger.warning("K8s sandbox adapter: API unavailable (%s)", e)

    @property
    def env_registry(self) -> EnvironmentRegistry:
        return self._env_registry

    # ── Activity / logs ───────────────────────────────────────────────

    def _log_activity(self, agent_id: str, event: str, detail: Any = None):
        if agent_id not in self._activity_log:
            self._activity_log[agent_id] = deque(maxlen=200)
        self._activity_log[agent_id].append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "event": event,
            "detail": str(detail) if detail else "",
        })

    def get_activity_log(self, agent_id: str) -> list[dict]:
        return list(self._activity_log.get(agent_id, []))

    def get_pod_logs(self, agent_id: str, tail_lines: int = 200) -> dict:
        agent_def = self._agents.get(agent_id)
        if agent_def and agent_def.environment_id:
            return self._get_env_agent_logs(agent_def.environment_id, agent_id, tail_lines)
        return self._get_standalone_pod_logs(agent_id, tail_lines)

    def _get_standalone_pod_logs(self, agent_id: str, tail_lines: int = 200) -> dict:
        pod_name = ""
        logs = ""
        status = "unknown"

        if not self._k8s_available:
            return {"agent_id": agent_id, "logs": self._log_cache.get(agent_id, "No K8s connection"),
                    "pod_name": "", "status": "unavailable"}

        try:
            pods = self._v1.list_namespaced_pod(
                SANDBOX_NAMESPACE,
                label_selector=f"forgeos.agent-id={agent_id}",
            )
            if pods.items:
                pod = pods.items[0]
                pod_name = pod.metadata.name
                status = pod.status.phase.lower() if pod.status and pod.status.phase else "unknown"
                logs = self._v1.read_namespaced_pod_log(
                    pod_name, SANDBOX_NAMESPACE, tail_lines=tail_lines
                )
                self._log_cache[agent_id] = logs
            else:
                logs = self._log_cache.get(agent_id, "")
                status = "terminated"
        except Exception:
            logs = self._log_cache.get(agent_id, "")
            status = "error"

        return {"agent_id": agent_id, "logs": logs, "pod_name": pod_name or "", "status": status}

    def _get_env_agent_logs(self, env_id: str, agent_id: str, tail_lines: int = 200) -> dict:
        env = self._env_registry.get(env_id)
        if not env or not env.service_url:
            return {"agent_id": agent_id, "logs": self._log_cache.get(agent_id, ""), "pod_name": "", "status": "unavailable"}
        try:
            resp = httpx.get(f"{env.service_url}/agents/{agent_id}/logs", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                self._log_cache[agent_id] = data.get("logs", "")
                return {"agent_id": agent_id, "logs": data.get("logs", ""), "pod_name": env.pod_name, "status": data.get("status", "unknown")}
        except Exception:
            pass
        return {"agent_id": agent_id, "logs": self._log_cache.get(agent_id, ""), "pod_name": env.pod_name if env else "", "status": "error"}

    # ── Environment CRUD ──────────────────────────────────────────────

    async def create_environment(self, env_def: EnvironmentDefinition) -> str:
        if not self._k8s_available:
            raise RuntimeError("K8s not available")

        self._env_registry.register(env_def)
        dep_name = f"forgeos-env-{env_def.env_id[:20]}"
        svc_name = dep_name

        env_vars = {
            "ENV_ID": env_def.env_id,
            "FORGEOS_API_URL": self._api_url,
            "PYTHONUNBUFFERED": "1",
        }
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            if os.environ.get(key):
                env_vars[key] = os.environ[key]

        env_list = [k8s_client.V1EnvVar(name=k, value=v) for k, v in env_vars.items()]

        pod_spec = k8s_client.V1PodSpec(
            restart_policy="Always",
            security_context=k8s_client.V1PodSecurityContext(
                run_as_non_root=True, run_as_user=1000, fs_group=1000,
            ),
            containers=[k8s_client.V1Container(
                name="env-manager",
                image=SANDBOX_IMAGE,
                image_pull_policy="Always",
                command=["python", "-m", "src.forgeos_sandbox.env_manager"],
                env=env_list,
                ports=[k8s_client.V1ContainerPort(container_port=MANAGER_PORT, name="manager")],
                resources=k8s_client.V1ResourceRequirements(
                    requests={"cpu": env_def.cpu_request, "memory": env_def.mem_request},
                    limits={"cpu": env_def.cpu_limit, "memory": env_def.mem_limit},
                ),
                security_context=k8s_client.V1SecurityContext(allow_privilege_escalation=False),
                volume_mounts=[
                    k8s_client.V1VolumeMount(name="files", mount_path="/app/files/knowledge"),
                    k8s_client.V1VolumeMount(name="tmp", mount_path="/tmp"),
                ],
                liveness_probe=k8s_client.V1Probe(
                    http_get=k8s_client.V1HTTPGetAction(path="/healthz", port=MANAGER_PORT),
                    initial_delay_seconds=10, period_seconds=30,
                ),
            )],
            volumes=[
                k8s_client.V1Volume(name="files", empty_dir=k8s_client.V1EmptyDirVolumeSource()),
                k8s_client.V1Volume(name="tmp", empty_dir=k8s_client.V1EmptyDirVolumeSource(size_limit="64Mi")),
            ],
        )

        labels = {
            "app.kubernetes.io/component": "environment",
            "app.kubernetes.io/part-of": "forgeos",
            "forgeos.env-id": env_def.env_id,
        }

        deployment = k8s_client.V1Deployment(
            metadata=k8s_client.V1ObjectMeta(name=dep_name, namespace=SANDBOX_NAMESPACE, labels=labels),
            spec=k8s_client.V1DeploymentSpec(
                replicas=1,
                selector=k8s_client.V1LabelSelector(match_labels={"forgeos.env-id": env_def.env_id}),
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(labels=labels),
                    spec=pod_spec,
                ),
            ),
        )

        service = k8s_client.V1Service(
            metadata=k8s_client.V1ObjectMeta(name=svc_name, namespace=SANDBOX_NAMESPACE, labels=labels),
            spec=k8s_client.V1ServiceSpec(
                selector={"forgeos.env-id": env_def.env_id},
                ports=[k8s_client.V1ServicePort(port=MANAGER_PORT, target_port=MANAGER_PORT, name="manager")],
                type="ClusterIP",
            ),
        )

        try:
            await asyncio.to_thread(self._apps_v1.create_namespaced_deployment, SANDBOX_NAMESPACE, deployment)
            await asyncio.to_thread(self._v1.create_namespaced_service, SANDBOX_NAMESPACE, service)
        except Exception as e:
            if "AlreadyExists" not in str(e):
                self._env_registry.unregister(env_def.env_id)
                raise

        env_def.pod_name = dep_name
        env_def.service_url = f"http://{svc_name}.{SANDBOX_NAMESPACE}.svc.cluster.local:{MANAGER_PORT}"
        env_def.status = EnvironmentStatus.PENDING
        logger.info("Environment created: %s (service=%s)", env_def.env_id, env_def.service_url)
        return env_def.env_id

    async def delete_environment(self, env_id: str):
        env = self._env_registry.get(env_id)
        if not env:
            return

        for agent_id in list(env.agent_ids):
            await self.stop_agent_in_env(env_id, agent_id)

        dep_name = f"forgeos-env-{env_id[:20]}"
        if self._k8s_available:
            try:
                await asyncio.to_thread(self._apps_v1.delete_namespaced_deployment, dep_name, SANDBOX_NAMESPACE)
            except Exception:
                pass
            try:
                await asyncio.to_thread(self._v1.delete_namespaced_service, dep_name, SANDBOX_NAMESPACE)
            except Exception:
                pass

        self._env_registry.unregister(env_id)
        logger.info("Environment deleted: %s", env_id)

    async def get_environment_status(self, env_id: str) -> dict:
        env = self._env_registry.get(env_id)
        if not env:
            return {"error": "not found"}

        pod_status = "unknown"
        if self._k8s_available:
            try:
                pods = self._v1.list_namespaced_pod(
                    SANDBOX_NAMESPACE, label_selector=f"forgeos.env-id={env_id}",
                )
                if pods.items:
                    pod = pods.items[0]
                    pod_status = pod.status.phase.lower() if pod.status else "unknown"
                    if pod_status == "running":
                        env.status = EnvironmentStatus.RUNNING
                    elif pod_status == "pending":
                        env.status = EnvironmentStatus.PENDING
            except Exception:
                pass

        agents_status = []
        if env.service_url and env.status == EnvironmentStatus.RUNNING:
            try:
                resp = await self._http.get(f"{env.service_url}/agents")
                if resp.status_code == 200:
                    agents_status = resp.json().get("agents", [])
            except Exception:
                pass

        return {
            "env_id": env_id,
            "name": env.name,
            "status": env.status.value,
            "pod_status": pod_status,
            "agent_ids": env.agent_ids,
            "agents": agents_status,
            "service_url": env.service_url,
        }

    # ── Agent-in-Environment operations ───────────────────────────────

    async def deploy_agent_to_env(self, env_id: str, agent_def: AgentDefinition,
                                   prompt: str = "", loop_mode: bool = False,
                                   loop_interval: int = 120) -> str:
        env = self._env_registry.get(env_id)
        if not env:
            raise ValueError(f"Environment {env_id} not found")
        if not env.service_url:
            raise RuntimeError(f"Environment {env_id} has no service URL")

        agent_def.environment_id = env_id
        self._agents[agent_def.agent_id] = agent_def

        token = self._tokens.mint(agent_def)
        config = {
            "agent_id": agent_def.agent_id,
            "agent_token": token,
            "api_url": self._api_url,
            "model": agent_def.llm_config.chat_model if agent_def.llm_config else "gpt-4o-mini",
            "provider": agent_def.llm_config.provider if agent_def.llm_config else "openai",
            "system_prompt": agent_def.system_prompt or "",
            "tools": agent_def.tools or [],
            "prompt": prompt or f"Standing duties for {agent_def.name}",
            "max_turns": (agent_def.metadata or {}).get("max_turns", 15),
            "loop_mode": loop_mode,
            "loop_interval": loop_interval,
        }

        retries = 0
        max_retries = 10
        while retries < max_retries:
            try:
                resp = await self._http.post(f"{env.service_url}/agents/start", json=config)
                if resp.status_code == 200:
                    self._env_registry.add_agent(env_id, agent_def.agent_id)
                    self._log_activity(agent_def.agent_id, "deployed_to_env", env_id)
                    logger.info("Agent %s deployed to environment %s", agent_def.agent_id, env_id)
                    return agent_def.agent_id
                data = resp.json()
                raise RuntimeError(data.get("error", f"HTTP {resp.status_code}"))
            except httpx.ConnectError:
                retries += 1
                logger.info("Environment %s not ready, retry %d/%d", env_id, retries, max_retries)
                await asyncio.sleep(3)

        raise RuntimeError(f"Environment {env_id} not reachable after {max_retries} retries")

    async def stop_agent_in_env(self, env_id: str, agent_id: str):
        env = self._env_registry.get(env_id)
        if env and env.service_url:
            try:
                await self._http.post(f"{env.service_url}/agents/{agent_id}/stop")
            except Exception:
                pass

        self._tokens.revoke(agent_id)
        self._env_registry.remove_agent(env_id, agent_id)
        agent_def = self._agents.get(agent_id)
        if agent_def:
            agent_def.environment_id = None
        self._log_activity(agent_id, "removed_from_env", env_id)
        logger.info("Agent %s removed from environment %s", agent_id, env_id)

    # ── AgentStackAdapter interface ───────────────────────────────────

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("K8s sandbox agent registered: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def invoke(self, agent_id, prompt, context=None, history=None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        start = time.time()

        if self._k8s_available and not agent_def.environment_id:
            return await self._invoke_in_pod(agent_def, prompt, start)

        if self._llm_router:
            return await self._invoke_via_platform(agent_id, agent_def, prompt, context, start, history)

        return AgentResult(
            agent_id=agent_id, status=AgentStatus.COMPLETED,
            output=f"[SIMULATED] K8s sandbox agent '{agent_def.name}' received: {prompt[:100]}",
            elapsed_ms=(time.time() - start) * 1000,
        )

    def _build_env(self, agent_def: AgentDefinition, prompt: str, loop_mode: bool = False) -> list:
        metadata = agent_def.metadata or {}
        env_dict = {
            "AGENT_ID": agent_def.agent_id,
            "AGENT_TOKEN": self._tokens.mint(agent_def),
            "FORGEOS_API_URL": self._api_url,
            "AGENT_MODEL": agent_def.llm_config.chat_model if agent_def.llm_config else "gpt-4o-mini",
            "AGENT_PROVIDER": agent_def.llm_config.provider if agent_def.llm_config else "openai",
            "AGENT_SYSTEM_PROMPT": agent_def.system_prompt or "",
            "AGENT_TOOLS": json.dumps(agent_def.tools or []),
            "AGENT_PROMPT": prompt,
            "AGENT_MAX_TURNS": str(metadata.get("max_turns", 15)),
            "PYTHONUNBUFFERED": "1",
        }
        if loop_mode:
            env_dict["AGENT_LOOP_MODE"] = "true"
            env_dict["AGENT_LOOP_INTERVAL"] = str(metadata.get("loop_interval_seconds", 120))

        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            if os.environ.get(key):
                env_dict[key] = os.environ[key]

        return [k8s_client.V1EnvVar(name=k, value=v) for k, v in env_dict.items()]

    def _build_pod_spec(self, name: str, agent_def: AgentDefinition, env_list: list, restart_policy: str = "Never") -> k8s_client.V1Pod:
        return k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=name,
                namespace=SANDBOX_NAMESPACE,
                labels={
                    "app.kubernetes.io/component": "sandbox",
                    "app.kubernetes.io/part-of": "forgeos",
                    "forgeos.agent-id": agent_def.agent_id,
                    "forgeos.owner-id": agent_def.owner_id or "",
                },
            ),
            spec=k8s_client.V1PodSpec(
                restart_policy=restart_policy,
                security_context=k8s_client.V1PodSecurityContext(
                    run_as_non_root=True, run_as_user=1000, fs_group=1000,
                ),
                containers=[k8s_client.V1Container(
                    name="agent",
                    image=SANDBOX_IMAGE,
                    image_pull_policy="Always",
                    env=env_list,
                    resources=k8s_client.V1ResourceRequirements(
                        requests={"cpu": SANDBOX_CPU_REQUEST, "memory": SANDBOX_MEM_REQUEST},
                        limits={"cpu": SANDBOX_CPU, "memory": SANDBOX_MEM},
                    ),
                    security_context=k8s_client.V1SecurityContext(allow_privilege_escalation=False),
                    volume_mounts=[
                        k8s_client.V1VolumeMount(name="knowledge", mount_path="/app/files/knowledge"),
                        k8s_client.V1VolumeMount(name="tmp", mount_path="/tmp"),
                    ],
                )],
                volumes=[
                    k8s_client.V1Volume(name="knowledge", empty_dir=k8s_client.V1EmptyDirVolumeSource()),
                    k8s_client.V1Volume(name="tmp", empty_dir=k8s_client.V1EmptyDirVolumeSource(size_limit="64Mi")),
                ],
            ),
        )

    async def _invoke_in_pod(self, agent_def: AgentDefinition, prompt: str, start: float) -> AgentResult:
        name = f"forgeos-sbx-{agent_def.agent_id[:20]}-{int(time.time()) % 10000}"
        env_list = self._build_env(agent_def, prompt)
        pod = self._build_pod_spec(name, agent_def, env_list)
        metadata = agent_def.metadata or {}
        max_duration = metadata.get("max_duration_seconds", 300)

        self._log_activity(agent_def.agent_id, "pod_created", name)

        try:
            self._v1.create_namespaced_pod(SANDBOX_NAMESPACE, pod)
            self._pod_names[agent_def.agent_id] = name
            logger.info("K8s sandbox pod created: %s", name)

            completed = await asyncio.wait_for(
                asyncio.to_thread(self._wait_for_pod, name),
                timeout=max_duration,
            )

            logs = self._v1.read_namespaced_pod_log(name, SANDBOX_NAMESPACE, tail_lines=50)
            self._log_cache[agent_def.agent_id] = logs

            try:
                self._v1.delete_namespaced_pod(name, SANDBOX_NAMESPACE)
            except Exception:
                pass

            self._tokens.revoke(agent_def.agent_id)
            elapsed = (time.time() - start) * 1000

            if completed:
                output = self._extract_output(logs)
                self._log_activity(agent_def.agent_id, "pod_completed", f"{elapsed:.0f}ms")
                return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.COMPLETED, output=output, elapsed_ms=elapsed)

            self._log_activity(agent_def.agent_id, "pod_failed", "non-zero exit")
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error="Pod failed", output=logs[-500:], elapsed_ms=elapsed)

        except asyncio.TimeoutError:
            logger.warning("K8s sandbox pod timed out: %s", name)
            try:
                self._v1.delete_namespaced_pod(name, SANDBOX_NAMESPACE)
            except Exception:
                pass
            self._tokens.revoke(agent_def.agent_id)
            self._log_activity(agent_def.agent_id, "pod_timeout", f"{max_duration}s")
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error=f"Timeout after {max_duration}s", elapsed_ms=(time.time() - start) * 1000)

        except Exception as e:
            logger.error("K8s sandbox error: %s", e)
            self._tokens.revoke(agent_def.agent_id)
            self._log_activity(agent_def.agent_id, "pod_error", str(e))
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error=str(e), elapsed_ms=(time.time() - start) * 1000)

    def _wait_for_pod(self, pod_name: str) -> bool:
        w = k8s_watch.Watch()
        for event in w.stream(
            self._v1.list_namespaced_pod, SANDBOX_NAMESPACE,
            field_selector=f"metadata.name={pod_name}", timeout_seconds=600,
        ):
            pod = event["object"]
            phase = pod.status.phase if pod.status else None
            if phase == "Succeeded":
                w.stop()
                return True
            if phase == "Failed":
                w.stop()
                return False
        return False

    async def _invoke_via_platform(self, agent_id, agent_def, prompt, context, start, history=None):
        from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
        tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
        result = await run_agentic_loop(
            llm_router=self._llm_router, llm_config=agent_def.llm_config,
            system_prompt=agent_def.system_prompt or f"You are {agent_def.name}.",
            user_prompt=prompt, tool_definitions=tools or None,
            tool_executor=self._tool_executor, agent_context=build_agent_context(agent_def, agent_id),
            context=context, history=history,
        )
        result.agent_id = agent_id
        result.elapsed_ms = (time.time() - start) * 1000
        return result

    async def start_loop(self, agent_id):
        agent_def = self._agents.get(agent_id)
        if not agent_def or not self._k8s_available:
            return

        if agent_def.environment_id:
            return

        dep_name = f"forgeos-sbx-{agent_id[:40]}"
        env_list = self._build_env(agent_def, f"Standing duties for {agent_def.name}", loop_mode=True)
        pod_template = self._build_pod_spec(dep_name, agent_def, env_list, restart_policy="Always")

        deployment = k8s_client.V1Deployment(
            metadata=k8s_client.V1ObjectMeta(
                name=dep_name, namespace=SANDBOX_NAMESPACE,
                labels={
                    "app.kubernetes.io/component": "sandbox",
                    "app.kubernetes.io/part-of": "forgeos",
                    "forgeos.agent-id": agent_def.agent_id,
                },
            ),
            spec=k8s_client.V1DeploymentSpec(
                replicas=1,
                selector=k8s_client.V1LabelSelector(
                    match_labels={"forgeos.agent-id": agent_def.agent_id},
                ),
                template=k8s_client.V1PodTemplateSpec(
                    metadata=pod_template.metadata, spec=pod_template.spec,
                ),
            ),
        )

        try:
            self._apps_v1.create_namespaced_deployment(SANDBOX_NAMESPACE, deployment)
            self._pod_names[agent_id] = dep_name
            self._log_activity(agent_id, "deployment_created", dep_name)
            logger.info("K8s sandbox deployment created: %s", dep_name)
        except Exception as e:
            if "AlreadyExists" in str(e):
                logger.info("K8s sandbox deployment already exists: %s", dep_name)
            else:
                logger.error("K8s sandbox deployment failed: %s", e)
                self._log_activity(agent_id, "deployment_error", str(e))

    async def stop(self, agent_id):
        agent_def = self._agents.get(agent_id)

        if agent_def and agent_def.environment_id:
            await self.stop_agent_in_env(agent_def.environment_id, agent_id)
            return

        if not self._k8s_available:
            return

        dep_name = f"forgeos-sbx-{agent_id[:40]}"
        try:
            self._apps_v1.delete_namespaced_deployment(dep_name, SANDBOX_NAMESPACE)
            logger.info("K8s sandbox deployment deleted: %s", dep_name)
        except Exception:
            pass

        try:
            pods = self._v1.list_namespaced_pod(
                SANDBOX_NAMESPACE, label_selector=f"forgeos.agent-id={agent_id}",
            )
            for pod in pods.items:
                try:
                    logs = self._v1.read_namespaced_pod_log(pod.metadata.name, SANDBOX_NAMESPACE, tail_lines=100)
                    self._log_cache[agent_id] = logs
                except Exception:
                    pass
                self._v1.delete_namespaced_pod(pod.metadata.name, SANDBOX_NAMESPACE)
        except Exception:
            pass

        self._tokens.revoke(agent_id)
        self._log_activity(agent_id, "stopped", "")

    async def shutdown(self):
        for aid in list(self._agents):
            await self.stop(aid)

    def get_status(self, agent_id):
        agent_def = self._agents.get(agent_id)
        if agent_def and agent_def.environment_id:
            env = self._env_registry.get(agent_def.environment_id)
            if env and env.status == EnvironmentStatus.RUNNING:
                return AgentStatus.RUNNING
            return AgentStatus.IDLE

        if not self._k8s_available:
            return AgentStatus.IDLE if agent_id in self._agents else AgentStatus.STOPPED

        try:
            pods = self._v1.list_namespaced_pod(
                SANDBOX_NAMESPACE, label_selector=f"forgeos.agent-id={agent_id}",
            )
            for pod in pods.items:
                phase = pod.status.phase if pod.status else None
                if phase in ("Running", "Pending"):
                    return AgentStatus.RUNNING
        except Exception:
            pass

        return AgentStatus.IDLE if agent_id in self._agents else AgentStatus.STOPPED

    def scaffold_files(self, agent_def):
        return {"sandbox-k8s.json": json.dumps({
            "agent_id": agent_def.agent_id,
            "image": SANDBOX_IMAGE,
            "namespace": SANDBOX_NAMESPACE,
            "environment_id": agent_def.environment_id,
        }, indent=2)}

    @staticmethod
    def _extract_output(logs):
        lines = [l for l in logs.strip().split("\n") if l.strip()]
        for line in reversed(lines):
            if "Done in" in line or "output=" in line.lower():
                return line
        return "\n".join(lines[-10:]) if lines else ""
