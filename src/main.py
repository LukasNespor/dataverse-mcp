"""
Dataverse MCP Server entrypoint.

Registers all tools with FastMCP and starts the server.

Environment variables (set via Docker or .env file):
  DATAVERSE_URL            (required) — e.g. https://yourorg.crm4.dynamics.com
  TENANT_ID                (optional) — Azure AD tenant ID, defaults to "common"
  CLIENT_ID                (required) — Azure AD app client ID from your Entra ID app registration
"""

import logging
import sys

import cache
from fastmcp import FastMCP
from tools import (
    tool_authenticate,
    tool_sign_out,
    tool_create_record,
    tool_delete_record,
    tool_get_schema,
    tool_invalidate_cache,
    tool_list_records,
    tool_update_record,
    tool_whoami,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # Log to stderr to avoid polluting MCP stdio protocol on stdout
)
logging.getLogger("mcp.shared.tool_name_validation").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# Load the app cache (WhoAmI + schema) from disk before serving any requests.
cache.load_from_disk()

mcp = FastMCP(
    name="Dataverse MCP Server",
    instructions="""
You are connected to a Microsoft Dataverse (CRM) environment via its Web API.
When the user says "CRM" or "Dataverse", they mean the same system.

AUTHENTICATION:
- Just call Dataverse tools directly. If a tool returns an error mentioning authentication,
  call `Sign_in_to_Dataverse` to get a sign-in URL and present it to the user.
  The token exchange happens automatically when the browser redirects — no second tool call is needed.
  Once the user confirms they have signed in, call `Get_my_identity` to verify the session
  and greet the user by their FullName (e.g. "Hello, John!").
- To sign out (e.g. to switch accounts), call `Sign_out_from_Dataverse`.

BEFORE CREATING OR UPDATING ANY RECORD:
1. Call `Get_table_schema` with the target table's LogicalName to retrieve field names,
   types, and required fields. Never guess field names — Dataverse is strict about them.
   Schema results are cached for 1 hour — you do not need to re-fetch them within a session.
2. Call `Get_my_identity` if the operation involves the current user's identity (owner, assignee, etc.).
   The result is cached permanently for the session — call it freely without worrying about cost.

TABLE AND FIELD NAMING:
- Table LogicalNames are singular lowercase: "appointment", "contact", "account", "lead".
- The entity set name used in API calls is usually the plural: "appointments", "contacts".
- Field LogicalNames are lowercase with underscores: "scheduledstart", "regardingobjectid".
- Never use display names (like "Start Time") in API calls — always use LogicalNames.

LOOKUP FIELDS:
- Lookup fields require OData bind syntax, not raw GUIDs:
  "ownerid@odata.bind": "/systemusers/<GUID>"
  "regardingobjectid_contact@odata.bind": "/contacts/<GUID>"

DATES:
- All DateTime values must be ISO 8601 UTC: "2024-06-15T14:30:00Z"

DELETION:
- Always confirm with the user before deleting. Prefer deactivating (statecode=1) over deletion
  for business records unless the user explicitly requests permanent deletion.

CACHE:
- If the user reports that field names or table structures seem wrong or outdated, call
  `Refresh_schema_cache` to force a fresh fetch from the API on the next schema request.
""",
)

mcp.tool(name="Sign_in_to_Dataverse", description=tool_authenticate.__doc__)(tool_authenticate)
mcp.tool(name="Sign_out_from_Dataverse", description=tool_sign_out.__doc__)(tool_sign_out)
mcp.tool(name="Get_my_identity", description=tool_whoami.__doc__)(tool_whoami)
mcp.tool(name="Get_table_schema", description=tool_get_schema.__doc__)(tool_get_schema)
mcp.tool(name="Refresh_schema_cache", description=tool_invalidate_cache.__doc__)(tool_invalidate_cache)
mcp.tool(name="List_records", description=tool_list_records.__doc__)(tool_list_records)
mcp.tool(name="Create_record", description=tool_create_record.__doc__)(tool_create_record)
mcp.tool(name="Update_record", description=tool_update_record.__doc__)(tool_update_record)
mcp.tool(name="Delete_record", description=tool_delete_record.__doc__)(tool_delete_record)

if __name__ == "__main__":
    import os
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    logger.info("Starting Dataverse MCP Server (transport=%s)", transport)
    if transport == "sse":
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        mcp.run(transport=transport, host=host, port=8000)
    else:
        mcp.run(transport=transport)
