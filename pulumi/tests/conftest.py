"""Ensure a current event loop exists before pulumi's @runtime.test helper
calls asyncio.get_event_loop() (Python 3.12+ no longer auto-creates one)."""

import asyncio

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
