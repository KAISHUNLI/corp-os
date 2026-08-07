"""Login rate limiter: Redis sliding window when available, else in-process."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from corp_os.services.redis_client import get_redis

logger = logging.getLogger(__name__)


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


class RedisRateLimiter:
    """Sliding window via Redis sorted set (multi-instance safe)."""

    PREFIX = "corp_os:ratelimit:"

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return True
        client = get_redis()
        if client is None:
            return MemoryRateLimiter().allow(key, limit=limit, window_seconds=window_seconds)

        now = time.time()
        window_start = now - float(window_seconds)
        rkey = f"{self.PREFIX}{key}"
        member = f"{now}:{uuid.uuid4().hex}"
        try:
            pipe = client.pipeline()
            pipe.zremrangebyscore(rkey, 0, window_start)
            pipe.zcard(rkey)
            removed, count = pipe.execute()
            if int(count) >= limit:
                return False
            pipe = client.pipeline()
            pipe.zadd(rkey, {member: now})
            pipe.expire(rkey, int(window_seconds) + 5)
            pipe.execute()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("Redis rate limit failed; falling back to memory")
            return self._memory_fallback(key, limit=limit, window_seconds=window_seconds)

    @staticmethod
    def _memory_fallback(key: str, *, limit: int, window_seconds: float) -> bool:
        return MemoryRateLimiter().allow(key, limit=limit, window_seconds=window_seconds)


class RateLimiter:
    """Facade: prefer Redis, transparent memory fallback."""

    def __init__(self) -> None:
        self._memory = MemoryRateLimiter()
        self._redis = RedisRateLimiter()

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        if get_redis() is not None:
            return self._redis.allow(key, limit=limit, window_seconds=window_seconds)
        return self._memory.allow(key, limit=limit, window_seconds=window_seconds)


# Backward-compatible alias used by older tests/imports
# MemoryRateLimiter kept public for unit tests without Redis.

login_rate_limiter = RateLimiter()
