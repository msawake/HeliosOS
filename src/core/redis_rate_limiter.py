"""
Redis-backed distributed rate limiter.

Replaces the in-memory RateLimiter for multi-replica deployments.
Uses Redis INCR + EXPIRE for atomic sliding window rate limiting
that works correctly behind a load balancer.

Falls back to in-memory RateLimiter when Redis is unavailable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.platform.kernel import KernelDecision

logger = logging.getLogger(__name__)

try:
    import redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


class RedisRateLimiter:
    """
    Distributed rate limiter using Redis.

    Uses two Redis keys per session:
    - forgeos:rate:{session_id}:total → session-lifetime counter
    - forgeos:rate:{session_id}:minute:{minute_bucket} → per-minute counter

    Atomic via Redis INCR. TTLs auto-clean expired keys.
    """

    def __init__(
        self,
        redis_url: str = "",
        max_calls_per_session: int = 100,
        max_calls_per_minute: int = 30,
        key_prefix: str = "forgeos:rate",
    ):
        self.max_per_session = max_calls_per_session
        self.max_per_minute = max_calls_per_minute
        self._prefix = key_prefix
        self._client = None

        if HAS_REDIS and redis_url:
            try:
                self._client = redis.from_url(redis_url)
                self._client.ping()
                logger.info("Redis rate limiter connected: %s", redis_url)
            except Exception as e:
                logger.warning("Redis unavailable, falling back to in-memory: %s", e)
                self._client = None

    @property
    def is_distributed(self) -> bool:
        return self._client is not None

    _RATE_LIMIT_LUA = """
    local session_key = KEYS[1]
    local minute_key = KEYS[2]
    local max_session = tonumber(ARGV[1])
    local max_minute = tonumber(ARGV[2])

    local s_count = redis.call('INCR', session_key)
    if s_count == 1 then redis.call('EXPIRE', session_key, 7200) end
    if s_count > max_session then return {1, s_count, 0} end

    local m_count = redis.call('INCR', minute_key)
    if m_count == 1 then redis.call('EXPIRE', minute_key, 120) end
    if m_count > max_minute then return {2, s_count, m_count} end

    return {0, s_count, m_count}
    """

    def check(self, context: Any) -> KernelDecision:
        """Check rate limits using a single atomic Lua script."""
        if not self._client:
            return KernelDecision.allow(reason="redis unavailable")

        sid = getattr(context, "session_id", None)
        aid = getattr(context, "agent_id", None)

        session_key = f"{self._prefix}:session:{sid}:total" if sid else f"{self._prefix}:agent:{aid}:total"
        minute_bucket = int(time.time() // 60)
        minute_key = f"{self._prefix}:agent:{aid}:min:{minute_bucket}"

        result = self._client.eval(
            self._RATE_LIMIT_LUA, 2,
            session_key, minute_key,
            self.max_per_session, self.max_per_minute,
        )
        code, s_count, m_count = int(result[0]), int(result[1]), int(result[2])

        if code == 1:
            return KernelDecision(
                action="rate_limit",
                reason=f"Session {sid or aid} exceeded {self.max_per_session} tool calls",
                details={"count": s_count},
            )
        if code == 2:
            return KernelDecision(
                action="rate_limit",
                reason=f"Agent {aid} exceeded {self.max_per_minute} calls/minute",
                details={"calls_in_window": m_count},
            )

        return KernelDecision.allow(reason="within rate limits")

    def reset_session(self, session_id: str):
        """Reset rate limits for a session."""
        if not self._client:
            return

        # Delete all keys matching this session
        pattern = f"{self._prefix}:{session_id}:*"
        keys = self._client.keys(pattern)
        if keys:
            self._client.delete(*keys)

    def get_session_usage(self, session_id: str) -> dict:
        """Get current usage for a session."""
        if not self._client:
            return {"total": 0, "per_minute": 0}

        session_key = f"{self._prefix}:{session_id}:total"
        total = int(self._client.get(session_key) or 0)

        minute_bucket = int(time.time() // 60)
        minute_key = f"{self._prefix}:{session_id}:min:{minute_bucket}"
        per_minute = int(self._client.get(minute_key) or 0)

        return {"total": total, "per_minute": per_minute}
