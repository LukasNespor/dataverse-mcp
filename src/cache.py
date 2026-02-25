"""
Persistent cache for WhoAmI identity and table schema data.

Two-layer caching strategy:
  1. In-memory dict (_mem) — zero-latency lookups within a single process run.
  2. JSON file on disk — survives container restarts.
     File permissions are set to 600 (owner rw only).

Cache file location: /data/token_cache_appcache.json (derived from the token cache path)

TTL policy:
  - WhoAmI: 24 hours (WHOAMI_CACHE_TTL_SECONDS). User settings like timezone
    can change, so periodic refresh is needed. Also invalidated explicitly
    when the user re-authenticates with a different account.
  - Table schema: configurable TTL via SCHEMA_CACHE_TTL_SECONDS (default 3600s / 1 hour).
    Dataverse schema changes are rare (require admin customization), so 1 hour is conservative.
    Set to 0 to disable schema caching entirely.
  - Table list: 24 hours (TABLES_CACHE_TTL_SECONDS). The set of tables in a Dataverse
    environment changes very rarely. Used by the List_tables tool for quick name lookups.

Cache file structure:
{
  "whoami": {
    "cached_at": 1718000000.0,
    "data": {
      "UserId": "...",
      "FullName": "...",
      "BusinessUnitId": "...",
      "OrganizationId": "...",
      "TimeZoneCode": 110,
      "TimeZoneName": "Central Europe Standard Time"
    }
  },
  "schema": {
    "appointment": {
      "cached_at": 1718000000.0,
      "data": { ...cleaned entity dict... }
    },
    "contact": { ... }
  },
  "tables": {
    "cached_at": 1718000000.0,
    "data": [ ...list of table dicts... ]
  }
}
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHOAMI_CACHE_TTL_SECONDS: int = 86400  # 24 hours
SCHEMA_CACHE_TTL_SECONDS: int = 3600   # 1 hour; set to 0 to disable
TABLES_CACHE_TTL_SECONDS: int = 86400  # 24 hours

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

# In-memory cache — populated on startup from disk, updated on every write
_mem: dict[str, Any] = {
    "whoami": None,       # dict | None
    "schema": {},         # {table_logical_name: {"cached_at": float, "data": dict}}
    "tables": None,       # {"cached_at": float, "data": list[dict]} | None
}

_dirty: bool = False      # True when in-memory state differs from last disk write


def _cache_path() -> Path:
    """Derive the app cache file path from the token cache path setting."""
    base = Path(settings.token_cache_path)
    return base.with_name(base.stem + "_appcache.json")


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def load_from_disk() -> None:
    """
    Load the app cache file from disk into _mem.

    Called once at server startup (from main.py before serving requests).
    Errors are logged and silently ignored — the server starts with an empty
    in-memory cache and will repopulate it on first use.
    """
    global _mem, _dirty
    path = _cache_path()

    if not path.exists():
        logger.info("No app cache file found at %s, starting with empty cache", path)
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))

        raw_whoami = data.get("whoami")
        # Migrate old format (plain dict) to new format — expire immediately
        # so the next whoami() call fetches fresh data with timezone fields
        if isinstance(raw_whoami, dict) and "cached_at" not in raw_whoami:
            raw_whoami = {"cached_at": 0, "data": raw_whoami}
        _mem["whoami"] = raw_whoami
        _mem["schema"] = data.get("schema", {})
        _mem["tables"] = data.get("tables")
        _dirty = False

        whoami_loaded = "yes" if _mem["whoami"] else "no"
        schema_tables = list(_mem["schema"].keys())
        tables_loaded = "yes" if _mem["tables"] else "no"
        logger.info(
            "App cache loaded from %s — whoami: %s, schema tables: %s, tables list: %s",
            path, whoami_loaded, schema_tables or "none", tables_loaded,
        )
    except Exception as e:
        logger.warning("Failed to load app cache from %s, starting empty: %s", path, e)


def save_to_disk() -> None:
    """
    Write the current in-memory cache to disk as JSON.

    Only writes if the cache has been modified since the last save (_dirty flag).
    Uses atomic temp-file + rename to prevent corrupt files on crash.
    File permissions are set to 600 (owner rw only).
    """
    global _dirty
    if not _dirty:
        return

    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        plaintext = json.dumps({
            "whoami": _mem["whoami"],
            "schema": _mem["schema"],
            "tables": _mem["tables"],
        }, indent=None)

        tmp.write_text(plaintext, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.rename(path)
        _dirty = False
        logger.debug("App cache saved to %s", path)
    except Exception as e:
        logger.error("Failed to save app cache to %s: %s", path, e)
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# WhoAmI cache
# ---------------------------------------------------------------------------

def get_whoami() -> Optional[dict]:
    """
    Return the cached WhoAmI result, or None if not cached or expired.

    Expiry is checked against WHOAMI_CACHE_TTL_SECONDS (24 hours).
    Call invalidate_whoami() when the user re-authenticates.
    """
    entry = _mem["whoami"]
    if entry is None:
        return None

    age = time.time() - entry["cached_at"]
    if age > WHOAMI_CACHE_TTL_SECONDS:
        logger.debug("WhoAmI cache expired (age=%.0fs)", age)
        return None

    return entry["data"]


def set_whoami(data: dict) -> None:
    """
    Store the WhoAmI result in memory and persist to disk.

    Call this immediately after a successful WhoAmI API call.
    cached_at is set to the current Unix timestamp for TTL calculation.
    """
    global _dirty
    _mem["whoami"] = {
        "cached_at": time.time(),
        "data": data,
    }
    _dirty = True
    save_to_disk()
    logger.debug("WhoAmI cached: UserId=%s", data.get("UserId"))


def invalidate_whoami() -> None:
    """
    Clear the cached WhoAmI result.

    Call this when the user re-authenticates
    so the next whoami() call fetches fresh identity data for the new account.
    """
    global _dirty
    _mem["whoami"] = None
    _dirty = True
    save_to_disk()
    logger.info("WhoAmI cache invalidated")


# ---------------------------------------------------------------------------
# Schema cache
# ---------------------------------------------------------------------------

def get_schema(table_name: str) -> Optional[dict]:
    """
    Return the cached schema for a single table, or None if not cached or expired.

    Expiry is checked against SCHEMA_CACHE_TTL_SECONDS. A TTL of 0 disables caching
    entirely (always returns None, forcing a fresh API fetch).
    """
    if SCHEMA_CACHE_TTL_SECONDS == 0:
        return None

    entry = _mem["schema"].get(table_name)
    if entry is None:
        return None

    age = time.time() - entry["cached_at"]
    if age > SCHEMA_CACHE_TTL_SECONDS:
        logger.debug("Schema cache expired for '%s' (age=%.0fs)", table_name, age)
        return None

    logger.debug("Schema cache hit for '%s' (age=%.0fs)", table_name, age)
    return entry["data"]


def set_schema(table_name: str, data: dict) -> None:
    """
    Store the schema for a single table in memory and persist to disk.

    Call this immediately after a successful schema API fetch.
    cached_at is set to the current Unix timestamp for TTL calculation.
    """
    global _dirty
    _mem["schema"][table_name] = {
        "cached_at": time.time(),
        "data": data,
    }
    _dirty = True
    save_to_disk()
    logger.debug("Schema cached for '%s'", table_name)


def invalidate_schema(table_name: Optional[str] = None) -> None:
    """
    Invalidate cached schema for a specific table, or all tables if table_name is None.

    Use this if you know the schema has changed (e.g. after a Dataverse customization).
    Normal TTL expiry handles stale schema automatically without needing to call this.
    """
    global _dirty
    if table_name:
        if table_name in _mem["schema"]:
            del _mem["schema"][table_name]
            _dirty = True
            save_to_disk()
            logger.info("Schema cache invalidated for '%s'", table_name)
    else:
        if _mem["schema"]:
            _mem["schema"] = {}
            _dirty = True
            save_to_disk()
            logger.info("Entire schema cache invalidated")


def get_cached_schema_table_names() -> list[str]:
    """Return the list of table names currently held in the schema cache (expired or not)."""
    return list(_mem["schema"].keys())


# ---------------------------------------------------------------------------
# Tables list cache
# ---------------------------------------------------------------------------

def get_tables() -> Optional[list[dict]]:
    """
    Return the cached table list, or None if not cached or expired.

    Expiry is checked against TABLES_CACHE_TTL_SECONDS (24 hours).
    """
    entry = _mem["tables"]
    if entry is None:
        return None

    age = time.time() - entry["cached_at"]
    if age > TABLES_CACHE_TTL_SECONDS:
        logger.debug("Tables list cache expired (age=%.0fs)", age)
        return None

    logger.debug("Tables list cache hit (age=%.0fs)", age)
    return entry["data"]


def set_tables(data: list[dict]) -> None:
    """
    Store the table list in memory and persist to disk.

    Call this immediately after a successful EntityDefinitions API fetch.
    """
    global _dirty
    _mem["tables"] = {
        "cached_at": time.time(),
        "data": data,
    }
    _dirty = True
    save_to_disk()
    logger.debug("Tables list cached (%d tables)", len(data))


def invalidate_tables() -> None:
    """
    Clear the cached table list.

    Called during full cache invalidation so the next List_tables call
    fetches a fresh list from the API.
    """
    global _dirty
    if _mem["tables"] is not None:
        _mem["tables"] = None
        _dirty = True
        save_to_disk()
        logger.info("Tables list cache invalidated")
