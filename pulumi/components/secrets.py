"""Secret Manager entries + IAM bindings.

DATABASE_URL and REDIS_URL come from `data.py` (auto-versioned).
The rest (LLM keys, MC password, Slack webhook) come from Pulumi config —
set with `pulumi config set --secret forgeos-gcp:anthropic_api_key …`.

If a config value is unset, the Secret resource is still created so ops can
add a version manually via `gcloud secrets versions add`.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


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
        self.mc_admin_password = self._secret(
            "mc-admin-password", config.get_secret("mc_admin_password")
        )
        self.slack_webhook_url = self._secret(
            "slack-webhook-url", config.get_secret("slack_webhook_url")
        )
        # MC proxy token. Auto-generated dev-… value if not overridden.
        self.api_token = self._secret(
            "api-token",
            config.get_secret("api_token") or _generate_api_token(),
        )
        # Tenant API key the MCP server presents to the platform API as
        # X-API-Key (validated against tenants.api_key_hash). Operator-supplied;
        # set with `pulumi config set --secret forgeos-gcp:mcp_api_key …` and
        # store its SHA-256 in the tenant row. Versionless until set, so the
        # MCP service only wires FORGEOS_API_KEY when a version exists.
        self.api_key = self._secret("api-key", config.get_secret("mcp_api_key"))
        # Jira credentials — referenced by leadforge config.yaml via the
        # secret:jira-* fallback chain. Stored as forgeos-jira-* in Secret
        # Manager and injected as JIRA_URL/JIRA_USERNAME/JIRA_API_TOKEN env
        # vars on platform-api (SecretsManager.get() falls back to env when
        # the bare secret name isn't found in Secret Manager).
        self.jira_url = self._secret("jira-url", config.get_secret("jira_url"))
        self.jira_username = self._secret("jira-username", config.get_secret("jira_username"))
        self.jira_api_token = self._secret("jira-api-token", config.get_secret("jira_api_token"))

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


def _generate_api_token() -> pulumi.Output[str]:
    import secrets as _stdsecrets

    return pulumi.Output.secret(f"dev-{_stdsecrets.token_urlsafe(24)}")
