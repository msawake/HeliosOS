# SPDX-License-Identifier: BUSL-1.1
# Backward-compat shim — real implementation lives in src.platform.kernel package.
from src.platform.kernel import (  # noqa: F401
    FEATURE_FLAG_ENV,
    STAGE_ORDER,
    Stage,
    Syscall,
    SyscallPipeline,
    make_audit_stage,
    make_boundary_stage,
    make_capability_stage,
    make_dispatch_stage,
    make_policy_stage,
    make_quota_stage,
    syscall_pipeline_enabled,
)
