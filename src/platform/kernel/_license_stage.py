# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
"""
Identity / license stage for the syscall pipeline.

First stage in the pipeline (``identity`` slot). Resolves the tenant
from the syscall context and checks license validity before any other
stage runs.
"""

from __future__ import annotations

from typing import Any


def make_license_stage(license_manager: Any) -> Any:
    """Factory for the identity/license pipeline stage.

    Follows the same pattern as ``make_capability_stage``,
    ``make_quota_stage``, etc. in ``_syscall.py``.
    """

    def _stage(syscall: Any) -> Any:
        lm = license_manager
        
        # In open-source core, if no license_manager is attached, we check the environment mode.
        # If running in production mode, we strictly deny execution and demand a license key.
        import os
        is_production = os.environ.get("FORGEOS_KERNEL_MODE", "").lower() == "production"
        
        if lm is None:
            if is_production:
                from src.platform.kernel._facade import KernelDecision
                return KernelDecision.deny(
                    reason="Production mode requires a valid Enterprise License Key. Visit https://makingscience.com to purchase a key."
                )
            return None

        tenant_id = (
            syscall.context.get("tenant_id")
            or syscall.args.get("tenant_id", "default")
        )

        decision = lm.check_license(tenant_id)

        syscall.context["tenant_id"] = tenant_id
        syscall.context["license_state"] = decision.details if hasattr(decision, "details") else {}

        if hasattr(decision, "denied") and decision.denied:
            return decision
        return None

    return _stage
