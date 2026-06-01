import os
import json
from typing import Any
import redis

_redis_pool: redis.ConnectionPool | None = None


def _get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _redis_pool = redis.ConnectionPool.from_url(url, decode_responses=False)
    return redis.Redis(connection_pool=_redis_pool)


def get_redis() -> redis.Redis:
    return _get_redis()


def set_cache(key: str, value: Any, ttl: int = 86400) -> None:
    _get_redis().set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)


def get_cache(key: str) -> Any:
    raw = _get_redis().get(key)
    if raw is None:
        return None
    return json.loads(raw)


def delete_cache(key: str) -> None:
    _get_redis().delete(key)
