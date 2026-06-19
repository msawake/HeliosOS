"""Keep a shadow Django ``User`` in sync with each ``TenantUser``.

So that (a) admins can log into Django admin and (b) the full Groups/Permissions
UI applies, every TenantUser gets a 1:1 Django User whose group membership tracks
the role string. The role string in ``tenant_users`` stays the source of truth
for tokens (auth.py); this signal mirrors it into Django's auth model.

Best-effort: wrapped so a missing auth table / managed=False quirk never breaks
the TenantUser write.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _sync_shadow_user(tenant_user) -> None:
    from django.contrib.auth.models import Group, User

    email = getattr(tenant_user, "email", None)
    role = getattr(tenant_user, "role", None)
    if not email or not role:
        return
    user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
    user.is_staff = role in ("admin", "operator")
    user.is_superuser = role == "admin"
    user.save(update_fields=["is_staff", "is_superuser"])
    group = Group.objects.filter(name=role).first()
    if group is not None:
        user.groups.set([group])


def _connect() -> None:
    from src.forgeos_web.tenancy.models import TenantUser

    @receiver(post_save, sender=TenantUser, dispatch_uid="forgeos_rbac_shadow_user")
    def _on_tenant_user_save(sender, instance, **kwargs):
        try:
            _sync_shadow_user(instance)
        except Exception:  # never break the primary write
            logger.exception("RBAC shadow-user sync failed for TenantUser")


_connect()
