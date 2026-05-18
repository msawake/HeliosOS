#!/usr/bin/env bash
# Build the ForgeOS Community Edition distribution (kernel-free).
#
# Usage: bash scripts/build_oss.sh [output_dir]
#
# Produces a clean copy of the repo with proprietary kernel files removed.
# The kernel stubs remain, so the platform runs without policy enforcement.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="${1:-${REPO_ROOT}/dist/forgeos-community}"

echo "=== ForgeOS Community Edition build ==="
echo "  Source: $REPO_ROOT"
echo "  Output: $OUTPUT_DIR"

# Clean output
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Copy everything except git, venv, caches, secrets
rsync -a --exclude='.git' \
         --exclude='.venv' \
         --exclude='__pycache__' \
         --exclude='*.pyc' \
         --exclude='.env' \
         --exclude='.env.local' \
         --exclude='config/google/credentials.json' \
         --exclude='infrastructure/docker/.env' \
         --exclude='node_modules' \
         --exclude='.next' \
         --exclude='openclaw2' \
         --exclude='dist' \
         "$REPO_ROOT/" "$OUTPUT_DIR/"

# Remove proprietary kernel implementation files
KERNEL_DIR="$OUTPUT_DIR/src/platform/kernel"
for f in _facade.py _syscall.py _capabilities.py _process.py _checkpoint.py; do
    rm -f "$KERNEL_DIR/$f"
    echo "  Removed: src/platform/kernel/$f"
done

# Remove kernel BSL license (community edition is fully Apache 2.0)
rm -f "$KERNEL_DIR/LICENSE"

# Verify the stubs work
echo ""
echo "=== Verifying imports with stubs ==="
cd "$OUTPUT_DIR"
python3 -c "
from src.platform.kernel import KernelDecision, Kernel, Phase, ProcessTable, Checkpoint
from src.platform.kernel import syscall_pipeline_enabled
from src.platform.process import Phase as P2
from src.platform.checkpoint import Checkpoint as C2
from src.platform.syscall import syscall_pipeline_enabled as spe2
from src.platform.capabilities import CapabilityManager
assert not syscall_pipeline_enabled(), 'syscall pipeline should be disabled in community edition'
print('All imports OK — community edition verified')
"

echo ""
echo "=== Build complete ==="
echo "  Output: $OUTPUT_DIR"
echo "  Size: $(du -sh "$OUTPUT_DIR" | cut -f1)"
