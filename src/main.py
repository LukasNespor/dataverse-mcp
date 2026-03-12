"""
Dataverse MCP Server entrypoint.

Registers all tools with FastMCP and starts the server using Entra ID
On-Behalf-Of (OBO) authentication via FastMCP AzureProvider.

Environment variables (set via Docker or .env file):
  DATAVERSE_URL            (required) — e.g. https://yourorg.crm4.dynamics.com
  TENANT_ID                (optional) — Azure AD tenant ID, defaults to "common"
  CLIENT_ID                (required) — Azure AD app client ID from your Entra ID app registration
  CLIENT_SECRET            (required) — Azure AD app client secret (confidential client)
  MCP_BASE_URL             (optional) — public URL of the server, default http://localhost:8000
  REDIS_URL                (required) — Redis connection string, e.g. redis://redis:6379/0
"""

import logging
import sys

import cache  # noqa: F401 — import establishes Redis connection at startup
from config import settings
from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth.providers.azure import AzureProvider
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from redis.asyncio import Redis as AsyncRedis
from tools import (
    tool_create_record,
    tool_delete_record,
    tool_confirm_delete_record,
    tool_get_schema,
    tool_invalidate_cache,
    tool_list_records,
    tool_list_tables,
    tool_update_record,
    tool_whoami,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("mcp.shared.tool_name_validation").setLevel(logging.ERROR)


class _Drop404Filter(logging.Filter):
    """Suppress Uvicorn access-log entries for 404 responses (bot scanner noise)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "404" not in getattr(record, "message", record.getMessage())


logging.getLogger("uvicorn.access").addFilter(_Drop404Filter())

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instructions text
# ---------------------------------------------------------------------------

_INSTRUCTIONS = """\
You are connected to a Microsoft Dataverse (CRM) environment via its Web API.
When the user says "CRM" or "Dataverse" or "Dynamics", they mean the same system.

AUTHENTICATION:
- You are already authenticated via Entra ID. No sign-in or sign-out tools are needed.
  Just call Dataverse tools directly. Call `Get_my_identity` to get the current user's identity.

BEFORE CREATING OR UPDATING ANY RECORD:
1. If you don't know the exact table LogicalName, call `List_tables` first.
   It returns a cached (24h) lightweight list of all tables with LogicalName, DisplayName,
   and EntitySetName — enough to find the right table name. This call is very cheap.
2. Call `Get_table_schema` with the target table's LogicalName to retrieve field names,
   types, and required fields. Never guess field names — Dataverse is strict about them.
   Schema results are cached for 1 hour — you do not need to re-fetch them within a session.
3. Call `Get_my_identity` if the operation involves the current user's identity (owner, assignee, etc.).
   The result is cached for 24 hours — call it freely without worrying about cost.

TABLE AND FIELD NAMING:
- Table LogicalNames are singular lowercase: "appointment", "contact", "account", "lead".
- The entity set name (EntitySetName) used in API URLs is usually the plural: "appointments", "contacts".
  If unsure of the EntitySetName, call `List_tables` to look it up.
- Field LogicalNames are lowercase with underscores: "scheduledstart", "regardingobjectid".
- Never use display names (like "Start Time") in API calls — always use LogicalNames.

LOOKUP FIELDS:
- Lookup fields require OData bind syntax, not raw GUIDs:
  "ownerid@odata.bind": "/systemusers/<GUID>"
  "regardingobjectid_contact@odata.bind": "/contacts/<GUID>"

DATES AND TIMEZONES:
- When the user specifies a time without an explicit timezone (e.g. "10 AM", "tomorrow at 3 PM"),
  treat it as local time in the user's timezone. Call `Get_my_identity` to get the user's
  `TimeZoneName` (e.g. "Central Europe Standard Time"), then convert to UTC before sending to the API.
- DateTime fields: ISO 8601 UTC with time, e.g. "2024-06-15T14:30:00Z"
- DateOnly fields: date part only, e.g. "2024-06-15"

APPOINTMENTS:
- When creating an appointment, ask the user if they want it to sync to Outlook.
- If yes, set the Organizer via activity parties (organizer is a PartyList field, not a Lookup):
  "appointment_activity_parties": [
    {{"partyid_systemuser@odata.bind": "/systemusers(<UserId>)", "participationtypemask": 7}}
  ]
  (call `Get_my_identity` to get the UserId). participationtypemask 7 = Organizer.
  This makes the appointment appear in the user's Outlook calendar via server-side sync.
- Use the same activity parties array for attendees:
  participationtypemask 5 = Required Attendee, 6 = Optional Attendee.

DELETION (two-step):
1. Call Delete_record(table, record_id) — this creates a proposal, does NOT delete.
2. Show the user the impact summary and ask for explicit confirmation.
3. Call Confirm_delete_record(proposal_id, confirm_token, confirm_phrase) to execute.
- Prefer deactivating (statecode=1) over deletion for business records unless the user
  explicitly requests permanent deletion.

CACHE:
- If the user reports that field names or table structures seem wrong or outdated, call
  `Refresh_schema_cache` to force a fresh fetch from the API on the next schema request.
"""

# ---------------------------------------------------------------------------
# Server setup — Azure OBO mode
# ---------------------------------------------------------------------------

logger.info("Starting Dataverse MCP Server (Azure OBO mode)")

azure_kwargs: dict = dict(
    client_id=settings.client_id,
    client_secret=settings.client_secret,
    tenant_id=settings.tenant_id,
    base_url=settings.mcp_base_url,
    required_scopes=settings.mcp_required_scopes,
    additional_authorize_scopes=[
        f"{settings.dataverse_url}/user_impersonation",
        "offline_access",
    ],
)

if settings.jwt_signing_key:
    azure_kwargs["jwt_signing_key"] = settings.jwt_signing_key

# Build the async Redis client ourselves because RedisStore's url=
# parser drops the rediss:// scheme, breaking SSL for Azure Cache.
_async_redis = AsyncRedis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_keepalive=True,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
)
_redis_store = RedisStore(client=_async_redis)

if settings.storage_encryption_key:
    azure_kwargs["client_storage"] = FernetEncryptionWrapper(
        key_value=_redis_store,
        fernet=Fernet(settings.storage_encryption_key),
    )
else:
    azure_kwargs["client_storage"] = _redis_store

auth_provider = AzureProvider(**azure_kwargs)

# Azure issues v1.0 tokens by default (accessTokenAcceptedVersion=null/1).
# v1.0 and v2.0 tokens differ in issuer and audience claims:
#   v1.0: iss=https://sts.windows.net/{tid}/         aud=api://{client_id}
#   v2.0: iss=https://login.microsoftonline.com/…/v2.0  aud={client_id}
# Patch the JWTVerifier to accept both formats so the server works
# regardless of the accessTokenAcceptedVersion manifest setting.
_tid = settings.tenant_id
_cid = settings.client_id
auth_provider._token_validator.issuer = [
    f"https://login.microsoftonline.com/{_tid}/v2.0",
    f"https://sts.windows.net/{_tid}/",
]
auth_provider._token_validator.audience = [
    _cid,
    f"api://{_cid}",
]

mcp = FastMCP(
    name="Dataverse MCP Server",
    auth=auth_provider,
    instructions=_INSTRUCTIONS,
)

# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

mcp.tool(name="Get_my_identity", description=tool_whoami.__doc__)(tool_whoami)

mcp.tool(name="List_tables", description=tool_list_tables.__doc__)(tool_list_tables)
mcp.tool(name="Get_table_schema", description=tool_get_schema.__doc__)(tool_get_schema)
mcp.tool(name="Refresh_schema_cache", description=tool_invalidate_cache.__doc__)(tool_invalidate_cache)

mcp.tool(name="List_records", description=tool_list_records.__doc__)(tool_list_records)
mcp.tool(name="Create_record", description=tool_create_record.__doc__)(tool_create_record)
mcp.tool(name="Update_record", description=tool_update_record.__doc__)(tool_update_record)
mcp.tool(name="Delete_record", description=tool_delete_record.__doc__)(tool_delete_record)
mcp.tool(name="Confirm_delete_record", description=tool_confirm_delete_record.__doc__)(tool_confirm_delete_record)

if __name__ == "__main__":
    logger.info("Starting Dataverse MCP Server (HTTP transport)")
    mcp.run(transport="http", host="0.0.0.0", port=8000, stateless_http=True)
