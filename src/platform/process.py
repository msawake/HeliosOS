# SPDX-License-Identifier: BUSL-1.1
# Backward-compat shim — real implementation lives in src.platform.kernel package.
from src.platform.kernel import (  # noqa: F401
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
