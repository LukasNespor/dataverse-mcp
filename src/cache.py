"""
App cache for WhoAmI identity, table schema, and table list data.

Uses Redis with native key expiry for all cache storage. Redis is required
and runs alongside the server in Docker (both local dev and Azure).

TTL policy:
  - WhoAmI: 24 hours. Invalidated on re-authentication / sign-out.
  - Table schema: 1 hour (configurable to 0 to disable).
  - Table list: 24 hours.

Public function signatures are unchanged — callers import and call
``cache.get_whoami()``, ``cache.set_schema()``, etc.
"""

import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHOAMI_CACHE_TTL_SECONDS: int = 86400  # 24 hours
SCHEMA_CACHE_TTL_SECONDS: int = 3600   # 1 hour; set to 0 to disable
TABLES_CACHE_TTL_SECONDS: int = 86400  # 24 hours

# Global cache key used for single-user (local) mode
_GLOBAL_KEY = "__global__"

# Redis key prefixes
_PREFIX_WHOAMI = "mcp:whoami:"
_PREFIX_SCHEMA = "mcp:schema:"
_KEY_TABLES = "mcp:tables"


# ---------------------------------------------------------------------------
# Redis connection
# ---------------------------------------------------------------------------

def _connect_redis():
    import redis as redis_lib
    r = redis_lib.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
    )
    r.ping()
    logger.info("Redis cache connected to %s", settings.redis_url.split("@")[-1])
    return r


_redis = _connect_redis()


def _scan_delete(pattern: str) -> int:
    """Delete all keys matching *pattern*. Returns count of deleted keys."""
    count = 0
    for key in _redis.scan_iter(match=pattern, count=100):
        _redis.delete(key)
        count += 1
    return count


# ---------------------------------------------------------------------------
# WhoAmI
# ---------------------------------------------------------------------------

def get_whoami(user_oid: Optional[str] = None) -> Optional[dict]:
    key = f"{_PREFIX_WHOAMI}{user_oid or _GLOBAL_KEY}"
    raw = _redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


def set_whoami(user_oid: Optional[str], data: dict) -> None:
    key = f"{_PREFIX_WHOAMI}{user_oid or _GLOBAL_KEY}"
    _redis.set(key, json.dumps(data), ex=WHOAMI_CACHE_TTL_SECONDS)
    logger.debug("WhoAmI cached in Redis for %s", user_oid or _GLOBAL_KEY)


def invalidate_whoami(user_oid: Optional[str] = None) -> None:
    if user_oid is not None:
        key = f"{_PREFIX_WHOAMI}{user_oid or _GLOBAL_KEY}"
        _redis.delete(key)
        logger.info("WhoAmI cache invalidated for %s", user_oid or _GLOBAL_KEY)
    else:
        count = _scan_delete(f"{_PREFIX_WHOAMI}*")
        logger.info("WhoAmI cache invalidated for all users (%d keys)", count)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def get_schema(table_name: str) -> Optional[str]:
    if SCHEMA_CACHE_TTL_SECONDS == 0:
        return None
    key = f"{_PREFIX_SCHEMA}{table_name}"
    raw = _redis.get(key)
    if raw is None:
        return None
    logger.debug("Schema cache hit for '%s' (Redis)", table_name)
    return raw


def set_schema(table_name: str, data: str) -> None:
    if SCHEMA_CACHE_TTL_SECONDS == 0:
        return
    key = f"{_PREFIX_SCHEMA}{table_name}"
    _redis.set(key, data, ex=SCHEMA_CACHE_TTL_SECONDS)
    logger.debug("Schema cached in Redis for '%s'", table_name)


def invalidate_schema(table_name: Optional[str] = None) -> None:
    if table_name:
        _redis.delete(f"{_PREFIX_SCHEMA}{table_name}")
        logger.info("Schema cache invalidated for '%s'", table_name)
    else:
        count = _scan_delete(f"{_PREFIX_SCHEMA}*")
        _redis.delete(_KEY_TABLES)
        logger.info("Entire schema cache invalidated (%d keys)", count)


# ---------------------------------------------------------------------------
# Tables list
# ---------------------------------------------------------------------------

def get_tables() -> Optional[str]:
    raw = _redis.get(_KEY_TABLES)
    if raw is None:
        return None
    logger.debug("Tables list cache hit (Redis)")
    return raw


def set_tables(data: str) -> None:
    _redis.set(_KEY_TABLES, data, ex=TABLES_CACHE_TTL_SECONDS)
    logger.debug("Tables list cached in Redis")


def invalidate_tables() -> None:
    _redis.delete(_KEY_TABLES)
    logger.info("Tables list cache invalidated")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_cached_schema_table_names() -> list[str]:
    prefix_len = len(_PREFIX_SCHEMA)
    return [
        key[prefix_len:]
        for key in _redis.scan_iter(match=f"{_PREFIX_SCHEMA}*", count=100)
    ]
