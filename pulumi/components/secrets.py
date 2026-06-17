"""Secret Manager entries + IAM bindings.

DATABASE_URL and REDIS_URL come from `data.py` (auto-versioned).
The rest (LLM keys, Slack webhook, Jira creds) come from Pulumi config —
set with `pulumi config set --secret forgeos-gcp:anthropic_api_key …`.

If a config value is unset, the Secret resource is still created so ops can
add a version manually via `gcloud secrets versions add`.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_random as random


class Secrets(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        project: pulumi.Input[str],
        database_url: pulumi.Input[str],
        redis_url: pulumi.Input[str] | None,
        config: pulumi.Config,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:secrets:Secrets", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        self._region = region
        self._child = child
        self.versions: dict[str, gcp.secretmanager.SecretVersion] = {}

        # Auto-populated from infra
        self.database_url = self._secret("database-url", database_url)
        self.redis_url = self._secret("redis-url", redis_url)

        # Operator-supplied (optional — empty placeholders if missing)
        self.anthropic_api_key = self._secret(
            "anthropic-api-key", config.get_secret("anthropic_api_key")
        )
        self.openai_api_key = self._secret(
            "openai-api-key", config.get_secret("openai_api_key")
        )
        self.gemini_api_key = self._secret(
            "gemini-api-key", config.get_secret("gemini_api_key")
        )
        self.slack_webhook_url = self._secret(
            "slack-webhook-url", config.get_secret("slack_webhook_url")
        )
        # Tenant API key the MCP server presents to the platform API as
        # X-API-Key (validated against tenants.api_key_hash). Operator-supplied;
        # set with `pulumi config set --secret forgeos-gcp:mcp_api_key …` and
        # store its SHA-256 in the tenant row. Versionless until set, so the
        # MCP service only wires FORGEOS_API_KEY when a version exists.
        self.api_key = self._secret("api-key", config.get_secret("mcp_api_key"))
        # Qwen (vLLM) gateway bearer key — agents on provider=vllm route through
        # the atlas-router gateway (VLLM_BASE_URL). Operator-supplied via
        # `pulumi config set --secret forgeos-gcp:vllm_api_key …`.
        self.vllm_api_key = self._secret("vllm-api-key", config.get_secret("vllm_api_key"))
        # Per-agent atlas/qwen key for `api_key_ref: secret:litellm-allycode-key`.
        # The credential store resolves that ref at the *platform* scope to the
        # GSM secret `forgeos-platform-litellm-allycode-key` (scoped_secret_name),
        # so the secret_id is `platform-litellm-allycode-key`. Operator-supplied via
        # `pulumi config set --secret forgeos-gcp:litellm_allycode_key …` — same
        # atlas-router key as vllm_api_key, kept separate so per-agent refs and the
        # default VLLM_API_KEY env can rotate independently.
        self.litellm_allycode_key = self._secret(
            "platform-litellm-allycode-key", config.get_secret("litellm_allycode_key")
        )
        # Jira credentials — referenced by leadforge config.yaml via the
        # secret:jira-* fallback chain. Stored as forgeos-jira-* in Secret
        # Manager and injected as JIRA_URL/JIRA_USERNAME/JIRA_API_TOKEN env
        # vars on platform-api (SecretsManager.get() falls back to env when
        # the bare secret name isn't found in Secret Manager).
        self.jira_url = self._secret("jira-url", config.get_secret("jira_url"))
        self.jira_username = self._secret("jira-username", config.get_secret("jira_username"))
        self.jira_api_token = self._secret("jira-api-token", config.get_secret("jira_api_token"))

        # Platform admin API key (FORGEOS_ADMIN_API_KEY) — recognized by
        # AuthManager as an ``admin`` principal so auth-enabled API/CLI access
        # works without seeding a DB row. Generated unless an operator supplies
        # `admin_api_key` in config. Always has a version → always wired.
        self.admin_api_key = self._secret(
            "admin-api-key",
            config.get_secret("admin_api_key")
            or random.RandomString(
                "forgeos-admin-api-key-gen", length=48, special=False, opts=child
            ).result,
        )
        # Dashboard login password (FORGEOS_DEV_PASSWORD) — the platform API's
        # /api/auth/token validates it for the dashboard's password login (gated
        # by FORGEOS_ALLOW_DEV_LOGIN). Generated unless `dashboard_password` set.
        self.dashboard_password = self._secret(
            "dashboard-password",
            config.get_secret("dashboard_password")
            or random.RandomPassword(
                "forgeos-dashboard-password-gen", length=20, special=False, opts=child
            ).result,
        )
        # HMAC secret for signed session tokens (FORGEOS_SESSION_SECRET).
        self.session_secret = self._secret(
            "session-secret",
            config.get_secret("session_secret")
            or random.RandomString(
                "forgeos-session-secret-gen", length=64, special=False, opts=child
            ).result,
        )
        # Bootstrap admin password — seeds a real admin login on a fresh deploy
        # (FORGEOS_BOOTSTRAP_ADMIN_PASSWORD; email via config bootstrap_admin_email).
        self.bootstrap_admin_password = self._secret(
            "bootstrap-admin-password",
            config.get_secret("bootstrap_admin_password")
            or random.RandomPassword(
                "forgeos-bootstrap-admin-password-gen", length=20, special=False, opts=child
            ).result,
        )

        self.register_outputs({})

    def _secret(
        self,
        secret_id: str,
        value: pulumi.Input[str] | None,
    ) -> gcp.secretmanager.Secret:
        secret = gcp.secretmanager.Secret(
            f"forgeos-{secret_id}",
            secret_id=f"forgeos-{secret_id}",
            replication=gcp.secretmanager.SecretReplicationArgs(
                user_managed=gcp.secretmanager.SecretReplicationUserManagedArgs(
                    replicas=[
                        gcp.secretmanager.SecretReplicationUserManagedReplicaArgs(
                            location=self._region,
                        )
                    ]
                )
            ),
            opts=self._child,
        )

        if value is not None:
            version = gcp.secretmanager.SecretVersion(
                f"forgeos-{secret_id}-v",
                secret=secret.id,
                secret_data=value,
                opts=self._child,
            )
            # Track so callers can express dependencies on the populated version
            # (Cloud Run validates secret_key_ref versions at create time).
            self.versions[secret_id] = version

        return secret

    def grant_access(
        self,
        name: str,
        secret: gcp.secretmanager.Secret,
        gsa_email: pulumi.Input[str],
    ) -> gcp.secretmanager.SecretIamMember:
        return gcp.secretmanager.SecretIamMember(
            name,
            secret_id=secret.id,
            role="roles/secretmanager.secretAccessor",
            member=pulumi.Output.concat("serviceAccount:", gsa_email),
            opts=pulumi.ResourceOptions(parent=self),
        )
