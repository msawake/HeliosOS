# SPDX-License-Identifier: BUSL-1.1
# Backward-compat shim — real implementation lives in src.platform.kernel package.
from src.platform.kernel import (  # noqa: F401
    CapabilityManager,
    CapabilityStore,
    CapabilityToken,
    InMemoryCapabilityStore,
)
