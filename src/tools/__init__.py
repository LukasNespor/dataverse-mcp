from dataclasses import dataclass
from typing import Optional

from tools.auth_tools import (
    tool_authenticate,
    tool_sign_out,
)
from tools.record_tools import (
    tool_list_records,
    tool_create_record,
    tool_update_record,
    tool_delete_record,
    tool_confirm_delete_record,
)
from tools.schema_tools import (
    tool_whoami,
    tool_list_tables,
    tool_get_schema,
    tool_invalidate_cache,
)

__all__ = [
    "tool_authenticate",
    "tool_sign_out",
    "tool_list_records",
    "tool_create_record",
    "tool_update_record",
    "tool_delete_record",
    "tool_confirm_delete_record",
    "tool_whoami",
    "tool_list_tables",
    "tool_get_schema",
    "tool_invalidate_cache",
    "TOOL_REGISTRY",
]


# ---------------------------------------------------------------------------
# Tool classification metadata (requirement §1 of Enterprise-architecture.md)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolMeta:
    tool_name: str
    category: str  # READ | CREATE | UPDATE | DESTRUCTIVE
    is_destructive: bool = False
    bulk_cap: Optional[int] = None
    description: Optional[str] = None


TOOL_REGISTRY: dict[str, ToolMeta] = {
    "Sign_in_to_Dataverse": ToolMeta(
        tool_name="Sign_in_to_Dataverse",
        category="READ",
        description="Interactive browser sign-in",
    ),
    "Sign_out_from_Dataverse": ToolMeta(
        tool_name="Sign_out_from_Dataverse",
        category="READ",
        description="Clear cached auth tokens",
    ),
    "Get_my_identity": ToolMeta(
        tool_name="Get_my_identity",
        category="READ",
        description="Return current user identity (WhoAmI)",
    ),
    "List_tables": ToolMeta(
        tool_name="List_tables",
        category="READ",
        description="List all Dataverse tables",
    ),
    "Get_table_schema": ToolMeta(
        tool_name="Get_table_schema",
        category="READ",
        description="Retrieve table field metadata",
    ),
    "Refresh_schema_cache": ToolMeta(
        tool_name="Refresh_schema_cache",
        category="READ",
        description="Invalidate cached schema so next fetch is fresh",
    ),
    "List_records": ToolMeta(
        tool_name="List_records",
        category="READ",
        bulk_cap=5000,
        description="Query records with OData filters",
    ),
    "Create_record": ToolMeta(
        tool_name="Create_record",
        category="CREATE",
        description="Create a new record in a table",
    ),
    "Update_record": ToolMeta(
        tool_name="Update_record",
        category="UPDATE",
        description="Partial-update (PATCH) an existing record",
    ),
    "Delete_record": ToolMeta(
        tool_name="Delete_record",
        category="DESTRUCTIVE",
        is_destructive=True,
        description="Propose permanent deletion of a record (two-step)",
    ),
    "Confirm_delete_record": ToolMeta(
        tool_name="Confirm_delete_record",
        category="DESTRUCTIVE",
        is_destructive=True,
        description="Execute a previously proposed deletion",
    ),
}
