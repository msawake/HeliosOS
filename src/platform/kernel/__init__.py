# SPDX-License-Identifier: Apache-2.0
"""
ForgeOS Kernel — policy engine, admission pipeline, capability tokens.

When the full kernel is installed, this package exposes the proprietary
implementation. When running the Community Edition (kernel files absent),
it falls back to permissive stubs that allow all operations without
policy enforcement.
"""

# -- Facade (Kernel, KernelDecision, AdmissionResult, subsystems) ----------
try:
    from src.platform.kernel._facade import (  # noqa: F401
        AdmissionController,
        AdmissionResult,
        BudgetManager,
        DataBoundaryManager,
        DecisionAction,
        Kernel,
        KernelDecision,
        PermissionManager,
        PolicyEngine,
    )
except ImportError:
    from src.platform.kernel_stubs._facade_stub import (  # type: ignore[assignment]  # noqa: F401
        AdmissionController,
        AdmissionResult,
        BudgetManager,
        DataBoundaryManager,
        DecisionAction,
        Kernel,
        KernelDecision,
        PermissionManager,
        PolicyEngine,
    )

# -- Syscall pipeline -----------------------------------------------------
try:
    from src.platform.kernel._syscall import (  # noqa: F401
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
except ImportError:
    from src.platform.kernel_stubs._syscall_stub import (  # type: ignore[assignment]  # noqa: F401
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

# -- Capabilities ---------------------------------------------------------
try:
    from src.platform.kernel._capabilities import (  # noqa: F401
        CapabilityManager,
        CapabilityStore,
        CapabilityToken,
        InMemoryCapabilityStore,
    )
except ImportError:
    from src.platform.kernel_stubs._capabilities_stub import (  # type: ignore[assignment]  # noqa: F401
        CapabilityManager,
        CapabilityStore,
        CapabilityToken,
        InMemoryCapabilityStore,
    )

# -- Process table ---------------------------------------------------------
try:
    from src.platform.kernel._process import (  # noqa: F401
        AgentIdentity,
        AgentProcess,
        Phase,
        ProcessTable,
        ResourceUsage,
        can_transition,
        is_terminal,
        phase_from_status_value,
        status_value_from_phase,
    )
except ImportError:
    from src.platform.kernel_stubs._process_stub import (  # type: ignore[assignment]  # noqa: F401
        AgentIdentity,
        AgentProcess,
        Phase,
        ProcessTable,
        ResourceUsage,
        can_transition,
        is_terminal,
        phase_from_status_value,
        status_value_from_phase,
    )

# -- Checkpoints -----------------------------------------------------------
try:
    from src.platform.kernel._checkpoint import (  # noqa: F401
        Checkpoint,
        CheckpointStore,
        LoopProgress,
        MemoryCheckpointStore,
        digest_messages,
    )
except ImportError:
    from src.platform.kernel_stubs._checkpoint_stub import (  # type: ignore[assignment]  # noqa: F401
        Checkpoint,
        CheckpointStore,
        LoopProgress,
        MemoryCheckpointStore,
        digest_messages,
    )
