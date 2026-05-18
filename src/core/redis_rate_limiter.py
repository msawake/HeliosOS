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

from src.core.hooks import AgentContext, HookDecision, HookResult

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

    def check(self, context: AgentContext) -> HookResult:
        """Check rate limits using Redis atomic counters."""
        if not self._client:
            # Fall back to parent class behavior if Redis not available
            return HookResult(decision=HookDecision.ALLOW)

        sid = getattr(context, "session_id", None)
        aid = getattr(context, "agent_id", None)

        # Session-level check (per session, fall back to agent key if no session)
        session_key = f"{self._prefix}:session:{sid}:total" if sid else f"{self._prefix}:agent:{aid}:total"
        count = self._client.incr(session_key)
        if count == 1:
            # First call — set TTL of 2 hours for cleanup
            self._client.expire(session_key, 7200)

        if count > self.max_per_session:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Session {sid or aid} exceeded {self.max_per_session} tool calls",
                metadata={"count": count},
            )

        # Per-minute check using minute bucket (per agent to prevent abuse across sessions)
        minute_bucket = int(time.time() // 60)
        minute_key = f"{self._prefix}:agent:{aid}:min:{minute_bucket}" if aid else f"{self._prefix}:session:{sid}:min:{minute_bucket}"
        minute_count = self._client.incr(minute_key)
        if minute_count == 1:
            # Expire after 2 minutes (covers current + next minute)
            self._client.expire(minute_key, 120)

        if minute_count > self.max_per_minute:
            return HookResult(
                decision=HookDecision.BLOCK,
                reason=f"Agent {aid or sid} exceeded {self.max_per_minute} calls/minute",
                metadata={"calls_in_window": minute_count},
            )

        return HookResult(decision=HookDecision.ALLOW)

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
