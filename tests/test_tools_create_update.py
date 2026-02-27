"""Tests for input validation on create and update tools."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tools.record_tools import tool_create_record, tool_update_record


VALID_TABLE = "contacts"
VALID_GUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
FAKE_TOKEN = "fake-access-token"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _mock_resolve_token():
    with patch("tools.record_tools.resolve_token", new_callable=AsyncMock, return_value=FAKE_TOKEN):
        yield


# ---------------------------------------------------------------------------
# tool_create_record — input validation
# ---------------------------------------------------------------------------

class TestCreateRecordValidation:
    def test_rejects_invalid_table_name(self):
        result = run(tool_create_record(table="bad table!", data={"name": "x"}, _obo_token=None))
        assert "Validation error" in result

    def test_rejects_empty_table_name(self):
        result = run(tool_create_record(table="", data={"name": "x"}, _obo_token=None))
        assert "Validation error" in result

    def test_accepts_valid_table_name(self):
        with patch("tools.record_tools.dataverse.create_record", new_callable=AsyncMock, return_value="some-guid"):
            result = run(tool_create_record(table=VALID_TABLE, data={"name": "x"}, _obo_token=None))
        assert "created successfully" in result


# ---------------------------------------------------------------------------
# tool_update_record — input validation
# ---------------------------------------------------------------------------

class TestUpdateRecordValidation:
    def test_rejects_invalid_table_name(self):
        result = run(tool_update_record(
            table="bad;table", record_id=VALID_GUID, data={"name": "x"}, _obo_token=None,
        ))
        assert "Validation error" in result

    def test_rejects_invalid_guid(self):
        result = run(tool_update_record(
            table=VALID_TABLE, record_id="not-a-guid", data={"name": "x"}, _obo_token=None,
        ))
        assert "Validation error" in result

    def test_rejects_both_invalid(self):
        result = run(tool_update_record(
            table="", record_id="bad", data={"name": "x"}, _obo_token=None,
        ))
        assert "Validation error" in result

    def test_accepts_valid_inputs(self):
        with patch("tools.record_tools.dataverse.update_record", new_callable=AsyncMock):
            result = run(tool_update_record(
                table=VALID_TABLE, record_id=VALID_GUID, data={"name": "x"}, _obo_token=None,
            ))
        assert "updated successfully" in result
