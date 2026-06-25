"""Helios OS GCP — lean stack.

Provisions only what the platform actually runs today: the backend API, the
web dashboard, the remote MCP endpoint, the durable worker tier, and the
data/identity/networking they depend on. Per-agent pod autoscaling (KEDA +
per-namespace agent isolation) and the standalone Mission Control service were
removed — agents run in-process in the platform-api / worker per-turn runtime.

Order of provisioning:
    1. Network          VPC + subnet + NAT + private services access
    2. Registry         Artifact Registry repo
    3. Data             Cloud SQL, Memorystore, Pub/Sub
    4. Identity         GSAs + project IAM roles
    5. Secrets          Secret Manager entries (DB URL, Redis URL, LLM keys, …)
    6. GKE              Autopilot cluster + k8s Provider
    7. Exec envs        forgeos-envs namespace + RBAC for kernel-gated env.exec
    8. Migrations       Cloud Run Job (idempotent SQL apply)
    9. Platform API     Cloud Run service (FastAPI)
   10. Worker tier      Always-on GKE Deployment (drains Redis queue, resumes HITL)
   11. MCP Server       Cloud Run service (FastMCP streamable-http)
   12. Dashboard        Cloud Run service (Next.js UI → platform API)
"""

from __future__ import annotations

import pulumi

from components.dashboard import Dashboard
from components.data import Data
from components.django_migrate import DjangoMigrate
from components.exec_environments import ExecEnvironments
from components.gke import Gke
from components.identity import Identity
from components.mcp_server import McpServer
from components.migrations import Migrations
from components.network import Network
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

# Durable runtime (per-turn worker tier) + kernel enforcement mode.
#   kernel_mode="production" turns on real kernel enforcement INCLUDING license
#   checks — it requires a license row for each tenant or every tool call is
#   denied "Unknown tenant". Leave unset/empty for permissive (local-dev) mode.
kernel_mode: str = config.get("kernel_mode") or ""
worker_replicas: int = config.get_int("worker_replicas") or 1

# Deletion protection for stateful resources (Cloud SQL). Set True for prod to
# prevent accidental `pulumi destroy` from dropping the database.
deletion_protection: bool = config.get_bool("deletion_protection") or False

# Environment label — used to tag Cloud Run services so dev/pre/pro are
# distinguishable in the GCP console and in billing exports.
environment: str = config.get("environment") or "dev"

# Image tags — set per deploy. Defaults assume `:latest` for first-boot bootstrap.
platform_api_tag: str = config.get("platform_api_tag") or "latest"
migrations_tag: str = config.get("migrations_tag") or "latest"
dashboard_tag: str = config.get("dashboard_tag") or "latest"
# The remote MCP server (src/forgeos_mcp) was removed from the repo, so the
# current platform-api image can't run `python -m src.forgeos_mcp`. Pin the
# forgeos-mcp service to its own tag (its last image that still had that code)
# so platform-api bumps don't break it. Defaults to platform_api_tag.
mcp_tag: str = config.get("mcp_tag") or platform_api_tag

# Qwen (vLLM) gateway — when set, agents on provider=vllm route here. The key
# rides Secret Manager (vllm-api-key); the URL is plain config.
vllm_base_url: str = config.get("vllm_base_url") or ""

# Drive scope for per-agent SA impersonation. Default (drive.file) only sees
# app-created files; set "drive" so agents can read/write folders SHARED with
# their SA (the treasury demo model). Applied to both the API and worker tiers.
drive_scopes: str = config.get("drive_scopes") or ""


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
    deletion_protection=deletion_protection,
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
    # Per-agent atlas key (secret:litellm-allycode-key) — resolved at runtime by
    # the credential store, so both the platform-api and agent-runtime GSAs need read.
    ("platform-litellm-allycode-key", secrets.litellm_allycode_key),
]
if enable_redis:
    _shared_secrets.append(("redis-url", secrets.redis_url))

for sa, label in [
    (identity.platform_api, "platform-api"),
    (identity.agent_runtime, "agent"),
]:
    for secret_name, secret in _shared_secrets:
        secrets.grant_access(f"{label}-{secret_name}-access", secret, sa.email)

secrets.grant_access("migrations-db-access", secrets.database_url, identity.migrations.email)
secrets.grant_access("mcp-api-key-access", secrets.api_key, identity.mcp.email)
# Auth secrets — platform-api only (worker/agent SAs don't authenticate users).
secrets.grant_access("platform-api-admin-key-access", secrets.admin_api_key, identity.platform_api.email)
secrets.grant_access("platform-api-dev-password-access", secrets.dashboard_password, identity.platform_api.email)
secrets.grant_access("platform-api-session-secret-access", secrets.session_secret, identity.platform_api.email)
secrets.grant_access("platform-api-bootstrap-admin-access", secrets.bootstrap_admin_password, identity.platform_api.email)

# 6. GKE
gke = Gke(
    "forgeos",
    region=region,
    network_id=network.network.id,
    subnet_id=network.subnet.id,
    deletion_protection=deletion_protection,
)

# 7. Exec-environment sandbox — the forgeos-envs namespace + RBAC that lets the
# platform-api drive per-agent `kubectl exec` sandbox pods (kernel-gated
# env.exec). Scoped: clusterViewer to authenticate + namespaced pod/exec RBAC.
exec_environments = ExecEnvironments(
    "forgeos",
    project=project,
    platform_api_gsa=identity.platform_api,
    k8s_provider=gke.provider,
)

# 8. Migrations — depends on the database-url SecretVersion (Cloud Run validates
# secret_key_ref :latest at create-time, so the version must exist first).
migrations = Migrations(
    "forgeos",
    region=region,
    image=_img("migrations", migrations_tag),
    gsa_email=identity.migrations.email,
    database_url_secret=secrets.database_url.id,
    vpc_network=network.network.id,
    vpc_subnet=network.subnet.id,
    environment=environment,
    opts=pulumi.ResourceOptions(depends_on=[secrets.versions["database-url"]]),
)

# Django migrate job (platform-api image) — applies the Django migration graph
# the raw-SQL job doesn't: auth/admin/sessions, django_celery_beat (Beat needs
# these), and the RunPython migrations (forgeos_rbac/rls/secrets/namespaces).
# Run once per deploy, BEFORE the worker + beat pods start.
django_migrate = DjangoMigrate(
    "forgeos",
    region=region,
    image=_img("platform-api", platform_api_tag),
    gsa_email=identity.platform_api.email,
    database_url_secret=secrets.database_url.id,
    vpc_network=network.network.id,
    vpc_subnet=network.subnet.id,
    opts=pulumi.ResourceOptions(depends_on=[secrets.versions["database-url"]]),
)

# 9. Platform API — only wire secrets that have an actual version. Cloud Run
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
    # Auth (enabled by default — the bootstrap CMD omits --no-auth):
    #   admin API key → AuthManager admin principal (API/CLI);
    #   dashboard password → /api/auth/token login (gated by FORGEOS_ALLOW_DEV_LOGIN).
    ("FORGEOS_ADMIN_API_KEY", "admin-api-key", secrets.admin_api_key),
    ("FORGEOS_DEV_PASSWORD", "dashboard-password", secrets.dashboard_password),
    ("FORGEOS_SESSION_SECRET", "session-secret", secrets.session_secret),
    ("FORGEOS_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-admin-password", secrets.bootstrap_admin_password),
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
    # Auth: bind the admin principal to this deployment's tenant, and enable the
    # dashboard's password login (/api/auth/token validates FORGEOS_DEV_PASSWORD).
    # Auth itself is on by default — the bootstrap CMD does not pass --no-auth.
    "FORGEOS_TENANT_ID": config.get("company") or "leadforge",
    "FORGEOS_ALLOW_DEV_LOGIN": "1",
    # Seed a real admin login on a fresh deploy (password is secret-backed above).
    "FORGEOS_BOOTSTRAP_ADMIN_EMAIL": config.get("bootstrap_admin_email") or "admin@forgeos.local",
}
if kernel_mode:
    _pa_extra_env["FORGEOS_KERNEL_MODE"] = kernel_mode
if vllm_base_url:
    _pa_extra_env["VLLM_BASE_URL"] = vllm_base_url
if drive_scopes:
    _pa_extra_env["FORGEOS_DRIVE_SCOPES"] = drive_scopes

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
    environment=environment,
    opts=pulumi.ResourceOptions(depends_on=_pa_deps),
)

# 10. Durable worker tier — always-on GKE Deployment that drains the Redis
# queue and resumes parked (HITL) runs. Gets the same env the app reads
# directly (DB/Redis + the configured LLM provider key), synced into a k8s
# Secret from the same Pulumi sources platform-api uses.
_worker_env_secrets: dict[str, pulumi.Input[str]] = {}
for _env_name, _cfg_key in [
    ("ANTHROPIC_API_KEY", "anthropic_api_key"),
    ("OPENAI_API_KEY", "openai_api_key"),
    ("GEMINI_API_KEY", "gemini_api_key"),
    ("VLLM_API_KEY", "vllm_api_key"),
    # Agents run on the worker now, so it needs the same MCP creds platform-api
    # had: the atlassian MCP resolves secret:jira-* via the env fallback
    # (jira-url -> JIRA_URL). Without these the MCP server starts with empty
    # creds and crashes (anyio "cancel scope" teardown) — see worker logs.
    ("JIRA_URL", "jira_url"),
    ("JIRA_USERNAME", "jira_username"),
    ("JIRA_API_TOKEN", "jira_api_token"),
    # Per-agent LLM key: every seeded agent's api_key_ref=secret:litellm-allycode-key
    # resolves via the env fallback (litellm-allycode-key -> LITELLM_ALLYCODE_KEY).
    # Unwired here, it resolved empty in the worker → atlas gateway 401 → agent
    # runs failed. (The IAM grant alone doesn't put it in the worker's env.)
    ("LITELLM_ALLYCODE_KEY", "litellm_allycode_key"),
]:
    _val = config.get_secret(_cfg_key)
    if _val is not None:
        _worker_env_secrets[_env_name] = _val
# Non-secret MCP toggle (atlassian read-only mode); silences the secret lookup
# warning and matches the local/.env default.
_worker_env_secrets["JIRA_READ_ONLY_MODE"] = "false"
# The gateway URL isn't secret, but ride the same env Secret so the worker's
# vLLM client targets it (agents on provider=vllm resolve their base_url here).
if vllm_base_url:
    _worker_env_secrets["VLLM_BASE_URL"] = vllm_base_url
if drive_scopes:
    _worker_env_secrets["FORGEOS_DRIVE_SCOPES"] = drive_scopes

worker = WorkerTier(
    "forgeos",
    image=_img("platform-api", platform_api_tag),
    project=project,
    k8s_provider=gke.provider,
    gke_cluster=gke.cluster,
    agent_runtime_gsa=identity.agent_runtime,
    database_url=data.database_url,
    redis_url=data.redis_url,
    env_secrets=_worker_env_secrets,
    kernel_mode=kernel_mode,
    replicas=worker_replicas,
    environment=environment,
)

# 11. MCP Server — remote MCP endpoint (FastMCP streamable-http) on the
# platform-api image, pointed at the platform API. Wires FORGEOS_API_KEY only
# when the api-key secret has a version (else the Service deploy would fail
# validating secret_key_ref :latest).
_mcp_api_key_secret = secrets.api_key.id if "api-key" in secrets.versions else None
_mcp_deps = [secrets.versions["api-key"]] if "api-key" in secrets.versions else []
mcp_server = McpServer(
    "forgeos",
    region=region,
    image=_img("platform-api", mcp_tag),
    gsa_email=identity.mcp.email,
    platform_api_url=platform_api.url,
    api_key_secret=_mcp_api_key_secret,
    environment=environment,
    opts=pulumi.ResourceOptions(depends_on=_mcp_deps),
)

# 12. Dashboard — Next.js web UI. Pure HTTP client of the platform API; its
# browser/SSR calls are rewritten to FORGEOS_API_URL (the platform-api URL).
dashboard = Dashboard(
    "forgeos",
    region=region,
    image=_img("forgeos-dashboard", dashboard_tag),
    gsa_email=identity.dashboard.email,
    platform_api_url=platform_api.url,
    environment=environment,
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
pulumi.export("mcp_server_url", mcp_server.url)
pulumi.export("dashboard_url", dashboard.url)
pulumi.export("migrations_job", migrations.job.name)
pulumi.export("django_migrate_job", django_migrate.job.name)
