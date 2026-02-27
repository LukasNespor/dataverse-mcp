"""Tests for src/cache.py — Redis-backed cache."""

import json
import time

import pytest

import cache
from cache import WHOAMI_CACHE_TTL_SECONDS, SCHEMA_CACHE_TTL_SECONDS, TABLES_CACHE_TTL_SECONDS


SAMPLE_WHOAMI = {"UserId": "uid-1", "FullName": "Jane Doe", "TimeZoneCode": 110}
SAMPLE_SCHEMA = "Table: contact (Contact)\nPrimary ID: contactid\n\nField | Display | Type | Req\nfirstname | First Name | String |"
SAMPLE_TABLES = "LogicalName | DisplayName | EntitySetName\naccount | Account | accounts"


# ---------------------------------------------------------------------------
# WhoAmI
# ---------------------------------------------------------------------------

class TestWhoAmI:
    def test_get_returns_none_when_empty(self):
        assert cache.get_whoami() is None

    def test_set_then_get(self):
        cache.set_whoami(None, SAMPLE_WHOAMI)
        result = cache.get_whoami()
        assert result == SAMPLE_WHOAMI

    def test_per_user_keying(self):
        cache.set_whoami("user-a", {"UserId": "a"})
        cache.set_whoami("user-b", {"UserId": "b"})
        assert cache.get_whoami("user-a")["UserId"] == "a"
        assert cache.get_whoami("user-b")["UserId"] == "b"
        assert cache.get_whoami("user-c") is None

    def test_invalidate_single_user(self):
        cache.set_whoami("user-a", {"UserId": "a"})
        cache.set_whoami("user-b", {"UserId": "b"})
        cache.invalidate_whoami("user-a")
        assert cache.get_whoami("user-a") is None
        assert cache.get_whoami("user-b") is not None

    def test_invalidate_all(self):
        cache.set_whoami("user-a", {"UserId": "a"})
        cache.set_whoami("user-b", {"UserId": "b"})
        cache.invalidate_whoami()
        assert cache.get_whoami("user-a") is None
        assert cache.get_whoami("user-b") is None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_get_returns_none_when_empty(self):
        assert cache.get_schema("contact") is None

    def test_set_then_get(self):
        cache.set_schema("contact", SAMPLE_SCHEMA)
        assert cache.get_schema("contact") == SAMPLE_SCHEMA

    def test_ttl_zero_disables_caching(self, monkeypatch):
        monkeypatch.setattr(cache, "SCHEMA_CACHE_TTL_SECONDS", 0)
        cache.set_schema("contact", SAMPLE_SCHEMA)
        assert cache.get_schema("contact") is None

    def test_invalidate_single_table(self):
        cache.set_schema("contact", SAMPLE_SCHEMA)
        cache.set_schema("account", "Table: account ...")
        cache.invalidate_schema("contact")
        assert cache.get_schema("contact") is None
        assert cache.get_schema("account") is not None

    def test_invalidate_all(self):
        cache.set_schema("contact", SAMPLE_SCHEMA)
        cache.set_schema("account", "Table: account ...")
        cache.invalidate_schema()
        assert cache.get_schema("contact") is None
        assert cache.get_schema("account") is None

    def test_get_cached_table_names(self):
        cache.set_schema("contact", SAMPLE_SCHEMA)
        cache.set_schema("account", "Table: account ...")
        names = cache.get_cached_schema_table_names()
        assert sorted(names) == ["account", "contact"]


# ---------------------------------------------------------------------------
# Tables list
# ---------------------------------------------------------------------------

class TestTables:
    def test_get_returns_none_when_empty(self):
        assert cache.get_tables() is None

    def test_set_then_get(self):
        cache.set_tables(SAMPLE_TABLES)
        assert cache.get_tables() == SAMPLE_TABLES

    def test_invalidate(self):
        cache.set_tables(SAMPLE_TABLES)
        cache.invalidate_tables()
        assert cache.get_tables() is None
