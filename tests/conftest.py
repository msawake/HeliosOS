# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures and kernel-skip logic."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "kernel: requires ForgeOS kernel (proprietary)")


def pytest_collection_modifyitems(config, items):
    try:
        from src.platform.kernel._facade import Kernel  # noqa: F401
    except ImportError:
        skip_kernel = pytest.mark.skip(reason="ForgeOS kernel not installed")
        for item in items:
            if "kernel" in item.keywords:
                item.add_marker(skip_kernel)
