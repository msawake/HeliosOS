"""ForgeOS GCP — full stack.

Order of provisioning:
    1. Network          VPC + subnet + NAT + private services access
    2. Registry         Artifact Registry repo
    3. Data             Cloud SQL, Memorystore, Pub/Sub
    4. Identity         GSAs + project IAM roles
    5. Secrets          Secret Manager entries (DB URL, Redis URL, LLM keys, …)
    6. GKE              Autopilot cluster + k8s Provider
    7. KEDA             Helm install in cluster
    8. Namespaces       one k8s ns per ForgeOS ns + KSA + WI + quotas + netpol
    9. Migrations       Cloud Run Job (idempotent SQL apply)
   10. Platform API     Cloud Run service (FastAPI :5099)
   11. Mission Control  Cloud Run service (FastAPI :8888 + bundled SPA)
   12. Agents           Deployment + ScaledObject per agent (optional, list driven)
   13. Observability    Managed Prometheus PodMonitoring per namespace
"""

from __future__ import annotations

import pulumi

from components.agent_base import AgentWorkload
from components.data import Data
from components.exec_environments import ExecEnvironments
from components.gke import Gke
from components.identity import Identity
from components.keda import Keda
from components.mcp_server import McpServer
from components.migrations import Migrations
from components.mission_control import MissionControl
from components.namespaces import Namespaces
from components.network import Network
from components.observability import Observability
from components.platform_api import PlatformApi
from components.registry import Registry
from components.secrets import Secrets
from components.worker import WorkerTier


config = pulumi.Config()
gcp_config = pulumi.Config("gcp")

project: str = gcp_config.require("project")
region: str = gcp_config.require("region")

network_cidr: str = config.require("network_cidr")
pods_cidr: str = config.require("pods_cidr")
services_cidr: str = config.require("services_cidr")
cloud_sql_tier: str = config.require("cloud_sql_tier")
enable_redis: bool = config.get_bool("enable_redis") or False
redis_memory_gb: int = config.get_int("redis_memory_gb") or 1
forgeos_namespaces: list[str] = config.require_object("namespaces")

# Durable runtime (per-turn worker tier) + kernel enforcement mode.
#   kernel_mode="production" turns on real kernel enforcement INCLUDING license
#   checks — it requires a license row for each tenant or every tool call is
#   denied "Unknown tenant". Leave unset/empty for permissive (local-dev) mode.
kernel_mode: str = config.get("kernel_mode") or ""
worker_replicas: int = config.get_int("worker_replicas") or 1

# Image tags — set per deploy. Defaults assume `:latest` for first-boot bootstrap.
platform_api_tag: str = config.get("platform_api_tag") or "latest"
mc_tag: str = config.get("mc_tag") or "latest"
agent_tag: str = config.get("agent_tag") or "latest"
migrations_tag: str = config.get("migrations_tag") or "latest"

# Qwen (vLLM) gateway — when set, agents on provider=vllm route here. The key
# rides Secret Manager (vllm-api-key); the URL is plain config.
vllm_base_url: str = config.get("vllm_base_url") or ""


# 1. Network
network = Network(
    "forgeos",
    region=region,
    network_cidr=network_cidr,
    pods_cidr=pods_cidr,
    services_cidr=services_cidr,
)

# 2. Registry
registry = Registry("forgeos", region=region, project=project)


def _img(name: str, tag: str) -> pulumi.Output[str]:
    return pulumi.Output.concat(registry.url, "/", name, ":", tag)


# 3. Data
data = Data(
    "forgeos",
    region=region,
    network_id=network.network.id,
    psa_dependency=network.psa_connection,
    cloud_sql_tier=cloud_sql_tier,
    enable_redis=enable_redis,
    redis_memory_gb=redis_memory_gb,
)

# 4. Identity
identity = Identity("forgeos", project=project)

# 5. Secrets
secrets = Secrets(
    "forgeos",
    region=region,
    project=project,
    database_url=data.database_url,
    redis_url=data.redis_url,
    config=config,
)

# Grant the right GSAs accessor on the right secrets
_shared_secrets = [
    ("database-url", secrets.database_url),
    ("anthropic-api-key", secrets.anthropic_api_key),
    ("openai-api-key", secrets.openai_api_key),
    ("gemini-api-key", secrets.gemini_api_key),
    ("slack-webhook-url", secrets.slack_webhook_url),
    ("jira-url", secrets.jira_url),
    ("jira-username", secrets.jira_username),
    ("jira-api-token", secrets.jira_api_token),
    ("vllm-api-key", secrets.vllm_api_key),
]
if enable_redis:
    _shared_secrets.append(("redis-url", secrets.redis_url))

for sa, label in [
    (identity.platform_api, "platform-api"),
    (identity.agent_runtime, "agent"),
]:
    for secret_name, secret in _shared_secrets:
        secrets.grant_access(f"{label}-{secret_name}-access", secret, sa.email)

secrets.grant_access("mc-pw-access", secrets.mc_admin_password, identity.mc.email)
secrets.grant_access("mc-api-token-access", secrets.api_token, identity.mc.email)
secrets.grant_access("migrations-db-access", secrets.database_url, identity.migrations.email)
secrets.grant_access("mcp-api-key-access", secrets.api_key, identity.mcp.email)

# 6. GKE
gke = Gke(
    "forgeos",
    region=region,
    network_id=network.network.id,
    subnet_id=network.subnet.id,
)

# 7. KEDA
keda = Keda("forgeos", k8s_provider=gke.provider)

# 8. Namespaces (depend on KEDA only if scalers in same `up`; safe to parallel)
namespaces = Namespaces(
    "forgeos",
    forgeos_namespaces=forgeos_namespaces,
    agent_runtime_gsa=identity.agent_runtime,
    identity=identity,
    k8s_provider=gke.provider,
)

# 8b. Exec-environment sandbox — the forgeos-envs namespace + RBAC that lets
# the platform-api drive per-agent `kubectl exec` sandbox pods (kernel-gated
# env.exec). Scoped: clusterViewer to authenticate + namespaced pod/exec RBAC.
exec_environments = ExecEnvironments(
    "forgeos",
    project=project,
    platform_api_gsa=identity.platform_api,
    k8s_provider=gke.provider,
)

# 9. Migrations — depends on the database-url SecretVersion (Cloud Run validates
# secret_key_ref :latest at create-time, so the version must exist first).
migrations = Migrations(
    "forgeos",
    region=region,
    image=_img("migrations", migrations_tag),
    gsa_email=identity.migrations.email,
    database_url_secret=secrets.database_url.id,
    vpc_network=network.network.id,
    vpc_subnet=network.subnet.id,
    opts=pulumi.ResourceOptions(depends_on=[secrets.versions["database-url"]]),
)

# 10. Platform API — only wire secrets that have an actual version. Cloud Run
# validates secret_key_ref :latest at revision deploy, so a versionless secret
# would fail Service creation. Users add versions later with
# `gcloud secrets versions add` and re-run `pulumi up`.
_pa_secret_specs = [
    ("DATABASE_URL", "database-url", secrets.database_url),
    ("ANTHROPIC_API_KEY", "anthropic-api-key", secrets.anthropic_api_key),
    ("OPENAI_API_KEY", "openai-api-key", secrets.openai_api_key),
    ("GEMINI_API_KEY", "gemini-api-key", secrets.gemini_api_key),
    ("SLACK_WEBHOOK_URL", "slack-webhook-url", secrets.slack_webhook_url),
    ("JIRA_URL", "jira-url", secrets.jira_url),
    ("JIRA_USERNAME", "jira-username", secrets.jira_username),
    ("JIRA_API_TOKEN", "jira-api-token", secrets.jira_api_token),
    ("VLLM_API_KEY", "vllm-api-key", secrets.vllm_api_key),
]
if enable_redis:
    _pa_secret_specs.append(("REDIS_URL", "redis-url", secrets.redis_url))

_pa_secret_refs: dict[str, pulumi.Input[str]] = {
    env: sec.id for env, key, sec in _pa_secret_specs if key in secrets.versions
}
_pa_deps = [secrets.versions[key] for _, key, _ in _pa_secret_specs if key in secrets.versions]

# Durable runtime env: enable the continuation engine + worker tier so invokes
# ENQUEUE to the shared Redis queue (the always-on GKE WorkerTier drains it).
_pa_extra_env: dict[str, pulumi.Input[str]] = {
    "FORGEOS_RUNTIME_V2": "1",
    "FORGEOS_RUNTIME_WORKERS": "1",
    "GCP_PROJECT_ID": project,
    # Exec-environment sandbox: target the forgeos-envs namespace and reach the
    # cluster via a kubeconfig materialized from this content (no creds inside —
    # auth is the gke-gcloud-auth-plugin using the platform-api GSA's ADC).
    "FORGEOS_ENV_NAMESPACE": "forgeos-envs",
    "FORGEOS_KUBE_CONTEXT": gke.cluster.name,
    "FORGEOS_KUBECONFIG_CONTENT": gke.kubeconfig,
}
if kernel_mode:
    _pa_extra_env["FORGEOS_KERNEL_MODE"] = kernel_mode
if vllm_base_url:
    _pa_extra_env["VLLM_BASE_URL"] = vllm_base_url

platform_api = PlatformApi(
    "forgeos",
    region=region,
    image=_img("platform-api", platform_api_tag),
    gsa_email=identity.platform_api.email,
    vpc_network=network.network.id,
    vpc_subnet=network.subnet.id,
    secret_refs=_pa_secret_refs,
    pubsub_topic=data.agent_triggers.name,
    extra_env=_pa_extra_env,
    opts=pulumi.ResourceOptions(depends_on=_pa_deps),
)

# 10b. Durable worker tier — always-on GKE Deployment that drains the Redis
# queue and resumes parked (HITL) runs. Gets the same env the app reads
# directly (DB/Redis + the configured LLM provider key), synced into a k8s
# Secret from the same Pulumi sources platform-api uses.
_worker_env_secrets: dict[str, pulumi.Input[str]] = {}
for _env_name, _cfg_key in [
    ("ANTHROPIC_API_KEY", "anthropic_api_key"),
    ("OPENAI_API_KEY", "openai_api_key"),
    ("GEMINI_API_KEY", "gemini_api_key"),
    ("VLLM_API_KEY", "vllm_api_key"),
]:
    _val = config.get_secret(_cfg_key)
    if _val is not None:
        _worker_env_secrets[_env_name] = _val
# The gateway URL isn't secret, but ride the same env Secret so the worker's
# vLLM client targets it (agents on provider=vllm resolve their base_url here).
if vllm_base_url:
    _worker_env_secrets["VLLM_BASE_URL"] = vllm_base_url

worker = WorkerTier(
    "forgeos",
    image=_img("platform-api", platform_api_tag),
    project=project,
    k8s_provider=gke.provider,
    agent_runtime_gsa=identity.agent_runtime,
    database_url=data.database_url,
    redis_url=data.redis_url,
    env_secrets=_worker_env_secrets,
    kernel_mode=kernel_mode,
    replicas=worker_replicas,
    opts=pulumi.ResourceOptions(depends_on=[keda.release]),
)

# 11. Mission Control
_mc_pw_secret = secrets.mc_admin_password.id if "mc-admin-password" in secrets.versions else None
_mc_api_token_secret = secrets.api_token.id if "api-token" in secrets.versions else None
_mc_deps = []
if "mc-admin-password" in secrets.versions:
    _mc_deps.append(secrets.versions["mc-admin-password"])
if "api-token" in secrets.versions:
    _mc_deps.append(secrets.versions["api-token"])
mc = MissionControl(
    "forgeos",
    region=region,
    image=_img("mc", mc_tag),
    gsa_email=identity.mc.email,
    platform_api_url=platform_api.url,
    mc_admin_password_secret=_mc_pw_secret,
    api_token_secret=_mc_api_token_secret,
    opts=pulumi.ResourceOptions(depends_on=_mc_deps),
)

# 11b. MCP Server — remote MCP endpoint (FastMCP streamable-http) on the
# platform-api image, pointed at the platform API. Wires FORGEOS_API_KEY only
# when the api-key secret has a version (else the Service deploy would fail
# validating secret_key_ref :latest).
_mcp_api_key_secret = secrets.api_key.id if "api-key" in secrets.versions else None
_mcp_deps = [secrets.versions["api-key"]] if "api-key" in secrets.versions else []
mcp_server = McpServer(
    "forgeos",
    region=region,
    image=_img("platform-api", platform_api_tag),
    gsa_email=identity.mcp.email,
    platform_api_url=platform_api.url,
    api_key_secret=_mcp_api_key_secret,
    opts=pulumi.ResourceOptions(depends_on=_mcp_deps),
)

# 12. Agents — list driven from config (empty by default; populate as agents ship)
declared_agents: list[dict] = config.get_object("agents") or []
agent_workloads: dict[str, AgentWorkload] = {}
for spec in declared_agents:
    agent_workloads[spec["name"]] = AgentWorkload(
        name=spec["name"],
        namespace=spec["namespace"],
        image=_img("agent-base", spec.get("tag", agent_tag)),
        manifest_ref=spec["manifest_ref"],
        pubsub_topic=data.agent_triggers.name,
        project=project,
        k8s_provider=gke.provider,
        platform_api_url=platform_api.url,
        cpu=spec.get("cpu", "250m"),
        memory=spec.get("memory", "512Mi"),
        always_on=spec.get("always_on", True),
        max_replicas=int(spec.get("max_replicas", 10)),
        opts=pulumi.ResourceOptions(depends_on=[keda.release, namespaces]),
    )

# 13. Observability
observability = Observability(
    "forgeos",
    forgeos_namespaces=forgeos_namespaces,
    k8s_provider=gke.provider,
    opts=pulumi.ResourceOptions(depends_on=[namespaces]),
)


# Exports
pulumi.export("vpc_id", network.network.id)
pulumi.export("subnet_id", network.subnet.id)
pulumi.export("artifact_registry_url", registry.url)
pulumi.export("sql_connection_name", data.sql_instance.connection_name)
pulumi.export("redis_host", data.redis.host if data.redis is not None else "disabled")
pulumi.export("pubsub_agent_triggers", data.agent_triggers.name)
pulumi.export("gke_cluster_name", gke.cluster.name)
pulumi.export("gke_endpoint", pulumi.Output.secret(gke.cluster.endpoint))
pulumi.export("platform_api_url", platform_api.url)
pulumi.export("mission_control_url", mc.url)
pulumi.export("mcp_server_url", mcp_server.url)
pulumi.export("migrations_job", migrations.job.name)
