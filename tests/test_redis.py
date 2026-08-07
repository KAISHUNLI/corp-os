"""Redis client and rate-limit integration tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corp_os.services.rate_limit import RedisRateLimiter
from corp_os.services.redis_client import get_redis, redis_ping, reset_redis_client


def test_redis_disabled(monkeypatch):
    monkeypatch.setenv("CORP_OS_REDIS_ENABLED", "false")
    from corp_os.config import get_settings

    get_settings.cache_clear()
    reset_redis_client()
    assert get_redis() is None
    assert redis_ping()["ok"] is False
    get_settings.cache_clear()
    reset_redis_client()


def test_redis_rate_limiter_pipeline(monkeypatch):
    monkeypatch.setenv("CORP_OS_REDIS_ENABLED", "true")
    from corp_os.config import get_settings

    get_settings.cache_clear()
    reset_redis_client()

    client = MagicMock()
    pipe1 = MagicMock()
    pipe1.execute.return_value = [0, 0]  # removed, count
    pipe2 = MagicMock()
    pipe2.execute.return_value = [1, True]
    client.pipeline.side_effect = [pipe1, pipe2]

    with patch("corp_os.services.rate_limit.get_redis", return_value=client):
        ok = RedisRateLimiter().allow("user:1", limit=5)
    assert ok is True
    assert pipe1.zremrangebyscore.called
    assert pipe2.zadd.called

    get_settings.cache_clear()
    reset_redis_client()


def test_redis_rate_limiter_blocks_at_limit(monkeypatch):
    client = MagicMock()
    pipe1 = MagicMock()
    pipe1.execute.return_value = [0, 5]  # already at limit
    client.pipeline.return_value = pipe1

    with patch("corp_os.services.rate_limit.get_redis", return_value=client):
        ok = RedisRateLimiter().allow("user:2", limit=5)
    assert ok is False
