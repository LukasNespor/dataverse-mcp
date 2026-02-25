"""
Dataverse record operation tools: list, create, update, delete.

IMPORTANT GUIDANCE FOR THE AGENT:
- If you don't know the exact table name, call `List_tables` first to look up the
  correct LogicalName and EntitySetName.
- Always call `Get_table_schema` before creating or updating a record
  to discover the correct field LogicalNames, types, and required fields.
- Always call `Get_my_identity` before creating records that require an owner or caller ID.
- The `table` parameter expects the EntitySetName (plural form, e.g. "appointments", "contacts").
  If unsure, call `List_tables` to find the correct EntitySetName.
- For lookup fields (e.g. ownerid, regardingobjectid), use the OData binding syntax:
  "ownerid@odata.bind": "/systemusers(<GUID>)" instead of setting the raw GUID.
- Record IDs are GUIDs in the format xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (no braces).
"""

import logging
from typing import Any, Optional

import dataverse
from auth import AuthenticationRequiredError

logger = logging.getLogger(__name__)


def _auth_error_message(tool_name: str) -> str:
    return (
        f"`{tool_name}` failed: not authenticated. "
        "Call `Sign_in_to_Dataverse` to sign in, then retry this tool."
    )


async def tool_list_records(
    table: str,
    filter: Optional[str] = None,
    select: Optional[str] = None,
    top: int = 50,
    orderby: Optional[str] = None,
    fetch_all_pages: bool = False,
) -> Any:
    """
    Query and return records from a Dataverse (CRM) table using OData query options.

    Use this tool when the user wants to search, list, or retrieve records from Dataverse/CRM.

    Parameters:
    - table (required): The entity set name of the table to query. This is usually the
      plural form of the LogicalName (e.g. "appointments", "contacts", "accounts").
      If unsure, call `List_tables` to find the correct EntitySetName.
    - filter (optional): OData $filter expression to restrict results.
      Examples:
        "statecode eq 0" — active records only
        "contains(subject,'meeting')" — subject contains 'meeting'
        "scheduledstart ge 2024-01-01T00:00:00Z" — starts after a date
        "ownerid/systemuserid eq <GUID>" — owned by a specific user
      Use single quotes around string values. Date values must be ISO 8601 UTC.
    - select (optional): Comma-separated list of field LogicalNames to include in results.
      Always specify this to reduce payload size. Example: "subject,scheduledstart,ownerid"
    - top (optional): Maximum number of records to return per page. Default 50, max 5000.
    - orderby (optional): OData $orderby expression. Example: "createdon desc"
    - fetch_all_pages (optional): If true, follows @odata.nextLink to retrieve all pages
      up to 20 pages. Use with caution on large datasets.

    Returns: A list of record objects. Each record contains the requested fields plus
    the primary ID field. Returns an empty list if no records match.
    """
    try:
        return await dataverse.list_records(
            table=table,
            filter_expr=filter,
            select=select,
            top=top,
            orderby=orderby,
            fetch_all_pages=fetch_all_pages,
        )
    except AuthenticationRequiredError:
        return _auth_error_message("list_records")
    except Exception as e:
        logger.exception("list_records failed for table %s", table)
        return f"Failed to list records from '{table}': {e}"


async def tool_create_record(table: str, data: dict) -> Any:
    """
    Create a new record in a Dataverse (CRM) table.

    MANDATORY STEPS before calling this tool:
    1. Call `List_tables` if you don't know the exact EntitySetName for the target table.
    2. Call `Get_table_schema` with the target table's LogicalName to retrieve:
       - Correct field LogicalNames (field names are case-sensitive)
       - RequiredLevel for each field (SystemRequired/ApplicationRequired fields MUST be provided)
       - AttributeType to format values correctly
    3. Call `Get_my_identity` if the record needs an owner, creator reference, or the current user's ID.

    Parameters:
    - table (required): The entity set name of the table (plural form, e.g. "appointments").
    - data (required): A dict of field LogicalName → value pairs.

    Field value formatting rules:
    - String fields: plain string value
    - Integer/Decimal fields: numeric value (no quotes)
    - DateTime fields: convert user-local times to UTC using the timezone from `Get_my_identity`,
      then format as ISO 8601 UTC string, e.g. "2024-06-15T14:30:00Z"
    - Boolean fields: true or false
    - OptionSet (picklist) fields: integer value of the option (get from schema or options endpoint)
    - Lookup fields: use OData binding syntax instead of a plain GUID:
        "regardingobjectid_contact@odata.bind": "/contacts(<GUID>)"
        "ownerid@odata.bind": "/systemusers(<GUID>)"
      The navigation property name and target entity set depend on the relationship —
      check schema or existing records for the correct format.
    - PartyList fields (organizer, requiredattendees, optionalattendees, etc.):
      These CANNOT be set as direct fields. Use the activity parties collection instead.
      Add "{entitylogicalname}_activity_parties" array to the data payload:
        "appointment_activity_parties": [
          {"partyid_systemuser@odata.bind": "/systemusers(<GUID>)", "participationtypemask": 7},
          {"partyid_contact@odata.bind": "/contacts(<GUID>)", "participationtypemask": 5}
        ]
      Participation type masks: 5 = Required Attendee, 6 = Optional Attendee, 7 = Organizer.
      For email: 1 = Sender (from), 2 = To, 3 = CC, 4 = BCC.

    Returns: The primary ID (GUID) of the created record (e.g. "a1b2c3d4-...").
    Save this ID if you need to reference, update, or delete this record later.
    """
    try:
        guid = await dataverse.create_record(table=table, data=data)
        return f"Record created successfully in '{table}'. ID: {guid}"
    except AuthenticationRequiredError:
        return _auth_error_message("create_record")
    except Exception as e:
        logger.exception("create_record failed for table %s", table)
        return f"Failed to create record in '{table}': {e}"


async def tool_update_record(table: str, record_id: str, data: dict) -> Any:
    """
    Update specific fields on an existing Dataverse (CRM) record using a partial PATCH update.

    Only the fields included in `data` are modified. All other fields are left unchanged.
    This is a non-destructive partial update — do NOT include fields you don't want to change.

    MANDATORY STEPS before calling this tool:
    1. Confirm you have the correct record_id (GUID). If unsure, call `List_records` first
       to find the record and extract its primary ID field.
    2. Call `Get_table_schema` if you are unsure of the correct field LogicalNames
       or value formats for the fields you intend to update.

    Parameters:
    - table (required): Entity set name of the table (plural form, e.g. "contacts").
    - record_id (required): GUID of the record to update, without braces.
      Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    - data (required): Dict of field LogicalName → new value. Only include fields to change.
      Follow the same value formatting rules as `create_record` (OData binding for lookups,
      ISO 8601 for dates, integer for option sets, etc.)

    Returns: A success confirmation message. Dataverse returns HTTP 204 on success (no body).
    If the record does not exist or you lack permission, an error is returned.
    """
    try:
        await dataverse.update_record(table=table, record_id=record_id, data=data)
        return f"Record {record_id} in '{table}' updated successfully."
    except AuthenticationRequiredError:
        return _auth_error_message("update_record")
    except Exception as e:
        logger.exception("update_record failed for table %s record %s", table, record_id)
        return f"Failed to update record {record_id} in '{table}': {e}"


async def tool_delete_record(table: str, record_id: str) -> Any:
    """
    Permanently delete a record from a Dataverse (CRM) table. This action cannot be undone.

    Before calling this tool:
    1. Confirm the user explicitly wants to delete — do not delete based on ambiguous intent.
    2. If you do not already have the record_id, call `List_records` to find the record
       and confirm it is the correct one before deleting.
    3. Consider whether deactivating (setting statecode=1) might be more appropriate
       than permanent deletion, especially for activities or core business records.

    Parameters:
    - table (required): Entity set name of the table (plural form, e.g. "leads").
    - record_id (required): GUID of the record to delete, without braces.
      Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    Returns: A success confirmation message. If the record does not exist or you lack
    delete privileges, an error is returned with details.
    """
    try:
        await dataverse.delete_record(table=table, record_id=record_id)
        return f"Record {record_id} in '{table}' deleted successfully."
    except AuthenticationRequiredError:
        return _auth_error_message("delete_record")
    except Exception as e:
        logger.exception("delete_record failed for table %s record %s", table, record_id)
        return f"Failed to delete record {record_id} in '{table}': {e}"
