"""Tests for the two-step delete workflow in src/tools/record_tools.py.

These tests mock the Dataverse API and token resolver to focus on the
propose/confirm logic, validation, and audit integration.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import confirmations
from confirmations import CONFIRM_PHRASE

# Import directly from the module to avoid pulling in auth_tools (needs msal).
from tools.record_tools import tool_confirm_delete_record, tool_delete_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_TABLE = "contacts"
VALID_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
FAKE_TOKEN = "fake-access-token"


def run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _fresh_store():
    """fakeredis is flushed automatically by the conftest autouse fixture."""


@pytest.fixture(autouse=True)
def _mock_resolve_token():
    """Mock resolve_token so tests don't need real auth."""
    with patch("tools.record_tools.resolve_token", new_callable=AsyncMock, return_value=FAKE_TOKEN):
        yield


# ---------------------------------------------------------------------------
# tool_delete_record (PROPOSE step)
# ---------------------------------------------------------------------------

class TestToolDeleteRecordPropose:
    def test_returns_proposal_fields(self):
        result = run(tool_delete_record(table=VALID_TABLE, record_id=VALID_GUID, _obo_token=None))
        assert "proposalId" in result
        assert "confirmToken" in result
        assert "confirmPhrase" in result
        assert CONFIRM_PHRASE in result

    def test_does_not_delete(self):
        with patch("tools.record_tools.dataverse.delete_record", new_callable=AsyncMock) as mock_del:
            run(tool_delete_record(table=VALID_TABLE, record_id=VALID_GUID, _obo_token=None))
            mock_del.assert_not_called()

    def test_returns_impact_summary(self):
        result = run(tool_delete_record(table=VALID_TABLE, record_id=VALID_GUID, _obo_token=None))
        assert "permanently delete" in result
        assert VALID_GUID in result
        assert VALID_TABLE in result

    def test_rejects_invalid_guid(self):
        result = run(tool_delete_record(table=VALID_TABLE, record_id="not-a-guid", _obo_token=None))
        assert "Validation error" in result

    def test_rejects_invalid_table(self):
        result = run(tool_delete_record(table="bad table!", record_id=VALID_GUID, _obo_token=None))
        assert "Validation error" in result

    def test_rejects_empty_table(self):
        result = run(tool_delete_record(table="", record_id=VALID_GUID, _obo_token=None))
        assert "Validation error" in result


# ---------------------------------------------------------------------------
# tool_confirm_delete_record (CONFIRM step)
# ---------------------------------------------------------------------------

class TestToolConfirmDeleteRecord:
    def _propose(self) -> tuple[str, str, str]:
        """Create a proposal and extract the three values from the result text."""
        result = run(tool_delete_record(table=VALID_TABLE, record_id=VALID_GUID, _obo_token=None))
        # Parse proposalId, confirmToken, confirmPhrase from markdown output
        lines = result.split("\n")
        pid = tok = phrase = None
        for line in lines:
            if "proposalId" in line:
                pid = line.split("`")[1]
            elif "confirmToken" in line:
                tok = line.split("`")[1]
            elif "confirmPhrase" in line:
                phrase = line.split("`")[1]
        assert pid and tok and phrase, f"Failed to parse proposal from: {result}"
        return pid, tok, phrase

    def test_executes_delete_on_valid_confirm(self):
        pid, tok, phrase = self._propose()
        with patch("tools.record_tools.dataverse.delete_record", new_callable=AsyncMock) as mock_del:
            result = run(tool_confirm_delete_record(
                proposal_id=pid, confirm_token=tok, confirm_phrase=phrase, _obo_token=None,
            ))
            mock_del.assert_called_once_with(
                table=VALID_TABLE, record_id=VALID_GUID, token=FAKE_TOKEN,
            )
        assert "deleted successfully" in result

    def test_replay_blocked(self):
        pid, tok, phrase = self._propose()
        with patch("tools.record_tools.dataverse.delete_record", new_callable=AsyncMock):
            run(tool_confirm_delete_record(
                proposal_id=pid, confirm_token=tok, confirm_phrase=phrase, _obo_token=None,
            ))
            result = run(tool_confirm_delete_record(
                proposal_id=pid, confirm_token=tok, confirm_phrase=phrase, _obo_token=None,
            ))
        assert "Confirmation failed" in result
        assert "already been used" in result

    def test_wrong_token_rejected(self):
        pid, _, phrase = self._propose()
        result = run(tool_confirm_delete_record(
            proposal_id=pid, confirm_token="wrong-token", confirm_phrase=phrase, _obo_token=None,
        ))
        assert "Confirmation failed" in result

    def test_wrong_phrase_rejected(self):
        pid, tok, _ = self._propose()
        result = run(tool_confirm_delete_record(
            proposal_id=pid, confirm_token=tok, confirm_phrase="WRONG", _obo_token=None,
        ))
        assert "Confirmation failed" in result
        assert "phrase mismatch" in result

    def test_nonexistent_proposal_rejected(self):
        result = run(tool_confirm_delete_record(
            proposal_id="no-such-id", confirm_token="tok", confirm_phrase=CONFIRM_PHRASE,
            _obo_token=None,
        ))
        assert "Confirmation failed" in result
        assert "No proposal found" in result

    def test_expired_proposal_rejected(self):
        pid, tok, phrase = self._propose()
        # Fast-forward the proposal's created_at in Redis
        import time
        stored = confirmations._get(pid)
        stored.created_at = time.time() - 9999
        confirmations._put(stored)
        result = run(tool_confirm_delete_record(
            proposal_id=pid, confirm_token=tok, confirm_phrase=phrase, _obo_token=None,
        ))
        assert "Confirmation failed" in result
        assert "expired" in result
