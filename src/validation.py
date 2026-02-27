"""
Input validators for Dataverse tool parameters.

Provides sanity checks for GUIDs and table names before they reach the API.
"""

import re


_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

_TABLE_NAME_MAX_LENGTH = 256


def validate_guid(value: str) -> str:
    """Validate that *value* is a well-formed UUID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).

    Returns the value unchanged on success, raises ValueError otherwise.
    """
    if not _GUID_RE.match(value):
        raise ValueError(
            f"Invalid GUID format: '{value}'. "
            "Expected format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        )
    return value


def validate_table_name(value: str) -> str:
    """Validate that *value* is a plausible Dataverse table/entity-set name.

    Rules: non-empty, alphanumeric + underscore only, max 256 chars.
    Returns the value unchanged on success, raises ValueError otherwise.
    """
    if not value:
        raise ValueError("Table name must not be empty.")
    if len(value) > _TABLE_NAME_MAX_LENGTH:
        raise ValueError(
            f"Table name too long ({len(value)} chars, max {_TABLE_NAME_MAX_LENGTH})."
        )
    if not _TABLE_NAME_RE.match(value):
        raise ValueError(
            f"Invalid table name: '{value}'. "
            "Only alphanumeric characters and underscores are allowed."
        )
    return value
