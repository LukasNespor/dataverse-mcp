"""
Schema discovery, user identity, and cache management tools.

These tools provide the metadata the agent needs to construct correct
Dataverse API calls — field names, types, required fields, and the
current authenticated user's identity.
"""

import logging
from typing import Any, Optional

import cache
import dataverse
from auth import AuthenticationRequiredError

logger = logging.getLogger(__name__)


async def tool_whoami() -> Any:
    """
    Return the identity of the currently authenticated Dataverse (CRM) user.

    Results are cached for 24 hours and persist across container restarts.
    Call this freely; it will not make a network request if the cache is warm.

    Call this tool when:
    - You need the current user's ID to set an owner field on a new record
      (e.g. creating an appointment, task, or phonecall "for me" or "assigned to me")
    - You need to filter records by the current user (e.g. "show my open cases")
    - You want to confirm which user account is active after authentication
    - Any operation that requires "systemuserid" or "ownerid" of the calling user

    Returns a dict with:
    - UserId (str): GUID of the authenticated Dataverse user. Use this when:
        - Setting ownerid: "ownerid@odata.bind": "/systemusers/<UserId>"
        - Filtering by owner: "$filter=_ownerid_value eq <UserId>"
        - Setting any field that expects the current user's systemuser GUID
    - FullName (str): Display name of the authenticated user (e.g. "John Doe").
      After successful sign-in, greet the user by their full name.
    - TimeZoneCode (int): The user's Dataverse timezone code (e.g. 110)
    - TimeZoneName (str): Windows timezone name (e.g. "Central Europe Standard Time").
      Use this to convert user-local times to UTC before sending DateTime values to Dataverse.
      When a user says "10 AM" without specifying a timezone, interpret it in their TimeZoneName
      timezone and convert to UTC.
    """
    try:
        return await dataverse.whoami()
    except AuthenticationRequiredError:
        return (
            "`whoami` failed: not authenticated. "
            "Call `Sign_in_to_Dataverse` to sign in, then retry."
        )
    except Exception as e:
        logger.exception("whoami failed")
        return f"Failed to retrieve user identity: {e}"


async def tool_list_tables() -> Any:
    """
    Return a compact pipe-delimited list of all tables (entities) in the Dataverse environment.

    Results are cached for 24 hours, so this call is very cheap after the first fetch.

    Output format (pipe-delimited text):
        LogicalName | DisplayName | EntitySetName
        account | Account | accounts
        contact | Contact | contacts
        ...

    - LogicalName: the singular API name to pass to `Get_table_schema` (e.g. "account")
    - DisplayName: the human-readable label shown in the CRM UI (e.g. "Account")
    - EntitySetName: the plural collection name used in API URLs (e.g. "accounts")

    Call this tool when:
    - You need to find the correct LogicalName for a table the user mentions by display name
    - You are unsure which EntitySetName to use for List_records, Create_record, etc.
    - You want to browse available tables in the environment

    After finding the table you need, call `Get_table_schema` with the LogicalName
    to get the full field-level metadata (attributes, types, required fields).
    """
    try:
        return await dataverse.list_tables()
    except AuthenticationRequiredError:
        return (
            "`list_tables` failed: not authenticated. "
            "Call `Sign in to Dataverse` to sign in, then retry."
        )
    except Exception as e:
        logger.exception("list_tables failed")
        return f"Failed to retrieve table list: {e}"


async def tool_get_schema(table_names: Optional[list[str]] = None) -> Any:
    """
    Retrieve the schema (entity definition and field metadata) for one or more Dataverse (CRM) tables.

    Results are cached for 1 hour per table and persist across container restarts.
    Subsequent calls for the same table within the TTL return instantly from cache
    without making any API requests. You can call this tool before every create/update
    without worrying about performance overhead once the schema is cached.

    Call this tool BEFORE creating or updating any record to ensure you use the correct:
    - Field LogicalNames (the API names, not display names — they differ and are case-sensitive)
    - Field types (determines how to format the value: string, int, DateTime, lookup, etc.)
    - Required fields (Req = "Y" means SystemRequired or ApplicationRequired — must be provided
      when creating a record, or the API will return a 400 error)
    - PrimaryIdAttribute (the GUID field name you receive after creation and need for updates/deletes)

    Also call this tool when:
    - The user asks about what fields a table has
    - You need to find the correct OptionSet integer values for a picklist field
    - If schema seems stale or wrong, call `Refresh_schema_cache` first then retry

    If you don't know the exact LogicalName or EntitySetName of a table, call `List_tables`
    first — it returns a cached lightweight index of all tables with their names.

    Parameters:
    - table_names (required): List of table LogicalNames to fetch schema for.
      LogicalName is the singular, lowercase API name — e.g. ["appointment", "contact", "account"].
      Always provide specific table names to get field-level details.

    Returns compact pipe-delimited text. Multiple tables are separated by "---".

    Output format:
        Table: account (Account)
        Primary ID: accountid
        Primary Name: name

        Field | Display Name | Type | Req
        name | Account Name | String | Y
        revenue | Annual Revenue | Money |
        primarycontactid | Primary Contact | Lookup |
        description | Description | Memo | — Main business description

    Columns:
    - Field: LogicalName — use in data payloads, $select, and $filter
    - Display Name: human-readable label
    - Type: data type — format values accordingly:
        String / Memo   → plain string
        Integer         → whole number
        Decimal / Money → decimal number
        DateTime        → ISO 8601 UTC string e.g. "2024-06-15T14:30:00Z"
        Boolean         → true / false
        Picklist / Status / State → integer option value
        Lookup          → OData bind: "fieldname@odata.bind": "/entityset(<GUID>)"
    - Req: "Y" = required on create (SystemRequired or ApplicationRequired), empty = optional
    - After " — ": optional description providing extra context about the field
    """
    try:
        return await dataverse.get_table_schema(table_names=table_names)
    except AuthenticationRequiredError:
        return (
            "`get_schema` failed: not authenticated. "
            "Call `Sign_in_to_Dataverse` to sign in, then retry."
        )
    except Exception as e:
        logger.exception("get_schema failed for %s", table_names)
        return f"Failed to retrieve schema for {table_names}: {e}"


async def tool_invalidate_cache(table_name: Optional[str] = None) -> str:
    """
    Invalidate cached schema data to force a fresh fetch from the Dataverse (CRM) API.

    Call this tool when:
    - The user reports that field names, table structure, or required fields seem wrong or outdated
    - You know that a Dataverse administrator has recently made customization changes
      (added/removed fields, changed required levels, renamed entities)
    - A create or update call fails with an unexpected "attribute does not exist" error
      and the schema cache may be serving stale data

    Do NOT call this routinely — schema changes in Dataverse require admin action and are rare.
    The schema cache has a 1-hour TTL and expires automatically under normal circumstances.

    Parameters:
    - table_name (optional): LogicalName of a specific table to invalidate (e.g. "appointment").
      If omitted, invalidates the entire schema cache for all tables.
      The WhoAmI identity cache is never affected by this tool.

    Returns a confirmation of what was invalidated. The next call to `get_schema`
    for the affected table(s) will fetch fresh data from the API and re-populate the cache.
    """
    if table_name:
        cache.invalidate_schema(table_name)
        return (
            f"Schema cache invalidated for table '{table_name}'. "
            f"The next call to `get_schema` for this table will fetch fresh data from the API."
        )
    else:
        cache.invalidate_schema()
        cache.invalidate_tables()
        return (
            "Entire schema cache invalidated (including table list). "
            "The next call to `Get_table_schema` or `List_tables` will fetch fresh data from the API."
        )
