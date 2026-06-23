"""Cloud SQL Postgres, Memorystore Redis, Pub/Sub topics."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_random as random


class Data(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        network_id: pulumi.Input[str],
        psa_dependency: pulumi.Resource,
        cloud_sql_tier: str,
        enable_redis: bool = False,
        redis_memory_gb: int = 1,
        deletion_protection: bool = False,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:data:Data", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, depends_on=[psa_dependency])

        # Cloud SQL Postgres 15 — private IP only
        self.sql_instance = gcp.sql.DatabaseInstance(
            f"{name}-pg",
            database_version="POSTGRES_15",
            region=region,
            deletion_protection=deletion_protection,
            settings=gcp.sql.DatabaseInstanceSettingsArgs(
                tier=cloud_sql_tier,
                availability_type="ZONAL",
                disk_size=20,
                disk_autoresize=True,
                ip_configuration=gcp.sql.DatabaseInstanceSettingsIpConfigurationArgs(
                    ipv4_enabled=False,
                    private_network=network_id,
                ),
                backup_configuration=gcp.sql.DatabaseInstanceSettingsBackupConfigurationArgs(
                    enabled=True,
                    start_time="03:00",
                ),
            ),
            opts=child,
        )

        self.database = gcp.sql.Database(
            f"{name}-db",
            instance=self.sql_instance.name,
            name="forgeos",
            opts=child,
        )

        # Stateful random password — generated once and stored in Pulumi state,
        # so it stays stable across previews/ups. (The old inline secrets.choice
        # ran at program eval, regenerating the password every run → the DB user,
        # the database-url secret, and the worker env that embeds it all churned.)
        # alphanumeric only (special=False) so it's safe inside the
        # postgresql://forgeos:<pw>@… URL.
        self._db_password = random.RandomPassword(
            f"{name}-db-pw",
            length=32,
            special=False,
            opts=pulumi.ResourceOptions(parent=self),
        )
        self.db_password = gcp.sql.User(
            f"{name}-db-user",
            instance=self.sql_instance.name,
            name="forgeos",
            password=self._db_password.result,
            opts=child,
        )

        # Memorystore Redis — optional. Helios OS falls back to in-memory when absent.
        self.redis: gcp.redis.Instance | None = None
        if enable_redis:
            self.redis = gcp.redis.Instance(
                f"{name}-redis",
                tier="BASIC",
                memory_size_gb=redis_memory_gb,
                region=region,
                authorized_network=network_id,
                connect_mode="PRIVATE_SERVICE_ACCESS",
                redis_version="REDIS_7_0",
                opts=child,
            )

        # Pub/Sub — one topic for agent triggers (KEDA scaler subscribes per agent)
        self.agent_triggers = gcp.pubsub.Topic(
            f"{name}-agent-triggers",
            opts=child,
        )

        # Connection strings (used by secrets.py to populate Secret Manager)
        self.database_url = pulumi.Output.all(
            self.sql_instance.private_ip_address,
            self.db_password.password,
        ).apply(
            lambda args: f"postgresql://forgeos:{args[1]}@{args[0]}:5432/forgeos"
        )
        self.redis_url: pulumi.Output[str] | None = (
            pulumi.Output.concat("redis://", self.redis.host, ":6379")
            if self.redis is not None
            else None
        )

        self.register_outputs(
            {
                "sql_connection_name": self.sql_instance.connection_name,
                "redis_host": self.redis.host if self.redis is not None else "",
            }
        )
