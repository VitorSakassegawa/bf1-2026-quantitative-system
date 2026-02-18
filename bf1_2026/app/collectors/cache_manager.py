"""Two-level cache: Redis (preferred) with JSON file fallback."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import settings

CACHE_DIR = Path("/tmp/bf1_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CacheStats:
    hits: int = 0
    misses: int = 0

    @classmethod
    def hit_rate(cls) -> float:
        total = cls.hits + cls.misses
        return cls.hits / total if total > 0 else 0.0


class CacheManager:
    """Two-level cache: Redis → local JSON file."""

    def __init__(self) -> None:
        self._redis = None
        self._redis_available: bool | None = None

    async def _get_redis(self):
        if self._redis_available is False:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url, decode_responses=True
            )
            await self._redis.ping()
            self._redis_available = True
            logger.debug("Redis cache connected")
            return self._redis
        except Exception as e:
            logger.warning(f"Redis unavailable, using file cache: {e}")
            self._redis_available = False
            return None

    @staticmethod
    def _cache_key(key: str) -> str:
        return f"bf1:{key}"

    @staticmethod
    def _file_path(key: str) -> Path:
        safe_key = hashlib.md5(key.encode()).hexdigest()
        return CACHE_DIR / f"{safe_key}.json"

    async def get(self, key: str) -> Any | None:
        """Get value from cache (Redis first, then file)."""
        redis_client = await self._get_redis()
        cache_key = self._cache_key(key)

        if redis_client:
            try:
                val = await redis_client.get(cache_key)
                if val is not None:
                    CacheStats.hits += 1
                    return json.loads(val)
            except Exception as e:
                logger.warning(f"Redis GET error: {e}")

        file_path = self._file_path(key)
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text())
                if data.get("expires_at", 0) > time.time():
                    CacheStats.hits += 1
                    return data["value"]
                else:
                    file_path.unlink(missing_ok=True)
            except (json.JSONDecodeError, KeyError):
                file_path.unlink(missing_ok=True)

        CacheStats.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Set value in cache with TTL (seconds)."""
        redis_client = await self._get_redis()
        cache_key = self._cache_key(key)
        serialized = json.dumps(value, default=str)

        if redis_client:
            try:
                await redis_client.setex(cache_key, ttl, serialized)
                return
            except Exception as e:
                logger.warning(f"Redis SET error: {e}")

        file_path = self._file_path(key)
        file_data = {
            "value": value,
            "expires_at": time.time() + ttl,
        }
        file_path.write_text(json.dumps(file_data, default=str))

    async def invalidate(self, key: str) -> None:
        """Remove a key from both cache levels."""
        redis_client = await self._get_redis()
        cache_key = self._cache_key(key)

        if redis_client:
            try:
                await redis_client.delete(cache_key)
            except Exception:
                pass

        file_path = self._file_path(key)
        file_path.unlink(missing_ok=True)

    def stats(self) -> dict:
        return {
            "hits": CacheStats.hits,
            "misses": CacheStats.misses,
            "hit_rate": CacheStats.hit_rate(),
        }


_cache = CacheManager()


def cached(ttl: int = 3600):
    """Decorator to cache async function results."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key from function name + args
            key_parts = [func.__module__, func.__qualname__]
            key_parts.extend(str(a) for a in args[1:])  # skip self
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            key = ":".join(key_parts)

            result = await _cache.get(key)
            if result is not None:
                return result

            result = await func(*args, **kwargs)
            await _cache.set(key, result, ttl)
            return result

        return wrapper

    return decorator
