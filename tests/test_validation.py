"""Tests for src/validation.py — GUID and table name validators."""

import pytest

from validation import validate_guid, validate_table_name


# ---------------------------------------------------------------------------
# validate_guid
# ---------------------------------------------------------------------------

class TestValidateGuid:
    def test_valid_lowercase(self):
        guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert validate_guid(guid) == guid

    def test_valid_uppercase(self):
        guid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
        assert validate_guid(guid) == guid

    def test_valid_mixed_case(self):
        guid = "a1B2c3D4-E5f6-7890-AbCd-eF1234567890"
        assert validate_guid(guid) == guid

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("")

    def test_rejects_too_short(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("a1b2c3d4-e5f6-7890-abcd")

    def test_rejects_braces(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("{a1b2c3d4-e5f6-7890-abcd-ef1234567890}")

    def test_rejects_no_hyphens(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("a1b2c3d4e5f67890abcdef1234567890")

    def test_rejects_non_hex(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("g1b2c3d4-e5f6-7890-abcd-ef1234567890")

    def test_rejects_wrong_segment_lengths(self):
        with pytest.raises(ValueError, match="Invalid GUID format"):
            validate_guid("a1b2c3d4-e5f6-7890-abcde-f1234567890")


# ---------------------------------------------------------------------------
# validate_table_name
# ---------------------------------------------------------------------------

class TestValidateTableName:
    def test_valid_simple(self):
        assert validate_table_name("contacts") == "contacts"

    def test_valid_with_underscore(self):
        assert validate_table_name("custom_entity") == "custom_entity"

    def test_valid_with_numbers(self):
        assert validate_table_name("cr4fd_mytable") == "cr4fd_mytable"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_table_name("")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_table_name("my table")

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_table_name("table;DROP")

    def test_rejects_slashes(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            validate_table_name("../../etc/passwd")

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="too long"):
            validate_table_name("a" * 257)

    def test_accepts_max_length(self):
        name = "a" * 256
        assert validate_table_name(name) == name
