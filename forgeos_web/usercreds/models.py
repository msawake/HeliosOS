"""Encrypted per-user/scoped credential ORM model — Phase A, managed=False.

Table: user_credentials (014_user_credentials.sql + alters in 018_scoped_secrets.sql).
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from forgeos_web.db import TenantModel


class UserCredential(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    user_id = models.TextField()
    kind = models.TextField()
    secret_name = models.TextField()
    enc_value = models.BinaryField(null=True)  # BYTEA Fernet ciphertext
    key_version = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)
    # Added in migration 018 (scoped secrets)
    scope = models.TextField(
        default="user",
        choices=[
            ("user", "user"),
            ("namespace", "namespace"),
            ("platform", "platform"),
        ],
    )
    namespace = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "user_credentials"
