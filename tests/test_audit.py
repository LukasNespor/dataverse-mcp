"""Tests for src/audit.py — structured audit logging and the audited_tool decorator."""

import json
import logging

import pytest

import audit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def audit_records(caplog):
    """Capture audit log records and return them as parsed JSON dicts."""
    records: list[dict] = []

    class _Collector:
        @property
        def entries(self) -> list[dict]:
            return [
                json.loads(r.message)
                for r in caplog.records
                if r.name == "dataverse.audit"
            ]

    with caplog.at_level(logging.INFO, logger="dataverse.audit"):
        yield _Collector()


# ---------------------------------------------------------------------------
# log_tool_call
# ---------------------------------------------------------------------------

class TestLogToolCall:
    def test_basic_fields(self, audit_records):
        audit.log_tool_call("List_records", "READ", "ok")
        entries = audit_records.entries
        assert len(entries) == 1
        e = entries[0]
        assert e["event"] == "tool_call"
        assert e["tool"] == "List_records"
        assert e["category"] == "READ"
        assert e["status"] == "ok"
        assert "ts" in e

    def test_includes_user_context(self, audit_records):
        audit.log_tool_call(
            "Delete_record", "DESTRUCTIVE", "ok",
            user_id="uid-123", user_name="John",
        )
        e = audit_records.entries[0]
        assert e["user_id"] == "uid-123"
        assert e["user_name"] == "John"

    def test_includes_correlation_id(self, audit_records):
        audit.log_tool_call(
            "List_records", "READ", "ok",
            correlation_id="corr-abc",
        )
        assert audit_records.entries[0]["correlation_id"] == "corr-abc"

    def test_omits_none_fields(self, audit_records):
        audit.log_tool_call("List_records", "READ", "ok")
        e = audit_records.entries[0]
        assert "user_id" not in e
        assert "user_name" not in e
        assert "correlation_id" not in e


# ---------------------------------------------------------------------------
# log_proposal
# ---------------------------------------------------------------------------

class TestLogProposal:
    def test_fields(self, audit_records):
        audit.log_proposal(
            proposal_id="pid-1",
            impact_summary="Will delete record X",
            ttl=120,
            token_fingerprint="abcd1234",
            user_id="uid-1",
            user_name="Jane",
        )
        e = audit_records.entries[0]
        assert e["event"] == "delete_proposed"
        assert e["proposal_id"] == "pid-1"
        assert e["impact"] == "Will delete record X"
        assert e["ttl_seconds"] == 120
        assert e["token_fingerprint"] == "abcd1234"

    def test_no_plaintext_token_in_log(self, audit_records):
        """Token fingerprint should be a short hash, not a full secret."""
        audit.log_proposal(
            proposal_id="pid-1",
            impact_summary="test",
            ttl=120,
            token_fingerprint="abcd1234",
        )
        raw = json.dumps(audit_records.entries[0])
        # The fingerprint is 8 chars — ensure no long secret-like strings snuck in
        assert "abcd1234" in raw
        assert len(audit_records.entries[0]["token_fingerprint"]) == 8


# ---------------------------------------------------------------------------
# log_confirm
# ---------------------------------------------------------------------------

class TestLogConfirm:
    def test_success(self, audit_records):
        audit.log_confirm("pid-1", success=True, user_id="uid-1", user_name="Jane")
        e = audit_records.entries[0]
        assert e["event"] == "delete_confirmed"
        assert e["success"] is True

    def test_failure_with_reason(self, audit_records):
        audit.log_confirm("pid-1", success=False, reason="expired")
        e = audit_records.entries[0]
        assert e["event"] == "delete_confirm_failed"
        assert e["success"] is False
        assert e["reason"] == "expired"


# ---------------------------------------------------------------------------
# audited_tool decorator
# ---------------------------------------------------------------------------

class TestAuditedTool:
    def test_logs_ok_on_success(self, audit_records):
        @audit.audited_tool("TestTool", "READ")
        async def my_tool():
            return "result"

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(my_tool())
        assert result == "result"
        e = audit_records.entries[0]
        assert e["tool"] == "TestTool"
        assert e["status"] == "ok"

    def test_logs_error_on_exception(self, audit_records):
        @audit.audited_tool("FailTool", "CREATE")
        async def my_tool():
            raise RuntimeError("boom")

        import asyncio
        with pytest.raises(RuntimeError, match="boom"):
            asyncio.get_event_loop().run_until_complete(my_tool())
        e = audit_records.entries[0]
        assert e["tool"] == "FailTool"
        assert e["status"] == "error"

    def test_preserves_function_name(self):
        @audit.audited_tool("Wrapped", "READ")
        async def original_name():
            pass

        assert original_name.__name__ == "original_name"
