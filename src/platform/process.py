# SPDX-License-Identifier: Apache-2.0
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
