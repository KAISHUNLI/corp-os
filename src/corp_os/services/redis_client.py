"""Shared Redis client with graceful memory fallback."""

from __future__ import annotations

import logging
import threading
from typing import Any

from corp_os.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any | None = None
_client_failed = False
_backend: str = "uninitialized"  # redis | memory | disabled


def redis_backend() -> str:
    return _backend


def reset_redis_client() -> None:
    """Test helper: drop cached client so next call re-resolves settings."""
    global _client, _client_failed, _backend
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = None
        _client_failed = False
        _backend = "uninitialized"


def get_redis():
    """Return a redis.Redis client, or None if disabled / unreachable."""
    global _client, _client_failed, _backend
    settings = get_settings()
    if not getattr(settings, "redis_enabled", True):
        _backend = "disabled"
        return None
    url = (settings.redis_url or "").strip()
    if not url:
        _backend = "disabled"
        return None
    with _lock:
        if _client is not None:
            return _client
        if _client_failed:
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            _client = client
            _backend = "redis"
            logger.info("Redis connected: %s", url.split("@")[-1])
            return _client
        except Exception as exc:  # noqa: BLE001
            _client_failed = True
            _backend = "memory"
            logger.warning("Redis unavailable (%s); using in-process fallback", exc)
            return None


def redis_ping() -> dict[str, Any]:
    """Health helper."""
    client = get_redis()
    if client is None:
        return {"ok": False, "backend": redis_backend()}
    try:
        ok = bool(client.ping())
        return {"ok": ok, "backend": "redis"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "backend": "redis", "error": str(exc)[:200]}
