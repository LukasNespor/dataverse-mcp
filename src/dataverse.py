"""
Dataverse Web API async client.

Wraps Microsoft Dataverse OData v4 REST API endpoints.
Handles token injection, error parsing, pagination, and transparent caching
of WhoAmI identity and table schema via cache.py.
"""

import logging
from typing import Any, Optional

import httpx

import cache
from auth import AuthenticationRequiredError
from config import settings

# Re-export so tools can import from dataverse
__all__ = ["AuthenticationRequiredError"]

logger = logging.getLogger(__name__)

# Maximum number of pages to follow when paginating list results
MAX_PAGES = 20

# System/internal attributes filtered from schema output to save tokens.
# These are rarely useful for typical CRM operations.
SYSTEM_ATTRIBUTES = {
    # Row version / concurrency
    "versionnumber",
    # Data import metadata
    "importsequencenumber", "overriddencreatedon",
    # Timezone internals
    "timezoneruleversionnumber", "utcconversiontimezonecode",
    # Delegation metadata (who acted on behalf of whom)
    "createdonbehalfby", "modifiedonbehalfby",
    # Internal ownership decomposition (ownerid is sufficient)
    "owningbusinessunit", "owningteam", "owninguser",
    # Exchange/sync internals
    "exchangerate",
    # Yomi (Japanese phonetic) fields
    "yominame", "yomifirstname", "yomilastname", "yomimiddlename", "yomifullname",
}

# Prefixes of internal/platform tables excluded from List_tables output.
# Users can still query these tables directly via List_records if needed.
_SYSTEM_TABLE_PREFIXES = (
    "msdyn_",       # Dynamics 365 internal modules
    "msdynmkt_",    # Dynamics 365 Marketing
    "mspp_",        # Power Pages
    "adx_",         # Portal
    "msfp_",        # Forms Pro / Customer Voice
    "mspcat_",      # Catalog
    "flow",         # flowsession, flowrun, flowmachine, flowlog, …
    "workflow",     # workflow, workflowbinary
    "sdk",          # sdkmessage, sdkmessageprocessingstep, …
    "plugin",       # plugintype, pluginpackage, …
    "import",       # importlog, importdata, importfile, …
    "bulkdelete",
    "asyncoperation",
    "solution",     # solutioncomponent, solutioncomponentdatasource, …
    "dependency",
    "ribbon",       # ribboncustomization, ribbonrule, …
    "postfollow",
    "postcomment",
    "postrule",
    "tracelog",
    "retent",       # retentionconfig, retentionoperation, …
    "powerbi",      # powerbidataset, powerbireport, …
    "powerpage",    # powerpagecomponent, powerpagesite, …
    "powerfx",      # powerfxrule
    "canvas",       # canvasapp
    "synapse",
    "searchtelemetry",
    "desktopflow",  # desktopflowbinary, desktopflowmodule
    "componentversion",
    "entityanalyticsconfig",
    "entityimageconfig",
    "entityindex",
    "virtualentitymetadata",
    "privilegechecker",
    "organizationdatasync",
    "recyclebinconfig",
    "callbackregistration",
    "serviceendpoint",
    "tdsmetadata",
    "workqueue",
    "fabricai",
    "featurecontrolsetting",
    "settingdefinition",
    "userentityinstancedata",
    "userentityuisettings",
    "subscriptionmanuallytrackedobject",
    "principalobjectattributeaccess",
    "fieldsecurityprofile",
    "fieldpermission",
)


def _is_system_table(logical_name: str) -> bool:
    """Return True if the table is a known system/platform table."""
    return logical_name.startswith(_SYSTEM_TABLE_PREFIXES)


def _parse_dataverse_error(response: httpx.Response) -> str:
    """
    Extract the most useful error message from a Dataverse OData error response.
    Dataverse wraps errors in: {"error": {"code": "...", "message": "..."}}
    """
    try:
        body = response.json()
        error = body.get("error", {})
        code = error.get("code", "unknown")
        message = error.get("message", response.text)
        return f"[{code}] {message}"
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:500]}"


def _get_headers(token: str) -> dict[str, str]:
    """Build authorization headers with the provided access token."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Content-Type": "application/json",
        "Prefer": 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"',
    }


async def _request(
    method: str,
    path: str,
    *,
    token: str,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> Optional[Any]:
    """
    Execute an authenticated HTTP request against the Dataverse Web API.

    Raises:
        httpx.HTTPStatusError: on 4xx/5xx responses (with Dataverse error message).
        httpx.RequestError: on network-level failures.
    """
    headers = _get_headers(token)
    if extra_headers:
        # Merge Prefer values instead of overwriting
        if "Prefer" in extra_headers and "Prefer" in headers:
            headers["Prefer"] = headers["Prefer"] + "," + extra_headers.pop("Prefer")
        headers.update(extra_headers)

    url = f"{settings.api_base}{path}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json,
        )

    if not response.is_success:
        error_msg = _parse_dataverse_error(response)
        logger.error("Dataverse API error %s %s: %s", method, url, error_msg)
        raise httpx.HTTPStatusError(
            error_msg,
            request=response.request,
            response=response,
        )

    # 204 No Content (update/delete responses) — return None
    if response.status_code == 204:
        return None

    return response.json()


# ---------------------------------------------------------------------------
# Public API methods
# ---------------------------------------------------------------------------


async def whoami(token: str, user_oid: Optional[str] = None) -> dict:
    """
    Return the identity of the currently authenticated Dataverse user.

    Results are cached for 24 hours per user_oid. The cache is invalidated
    automatically when the user re-authenticates. API is only called on first
    use, after cache expiry, or after explicit invalidation.

    Args:
        token: Bearer access token for Dataverse.
        user_oid: Entra ID object ID of the user (for per-user cache keying).
                  If None, uses a global cache key (local/single-user mode).

    Returns:
      - UserId (str): GUID of the authenticated user — use for owner/assignee fields.
      - FullName (str): Display name of the authenticated user.
      - TimeZoneCode (int): The user's Dataverse timezone code.
      - TimeZoneName (str): Windows timezone name (e.g. "Central Europe Standard Time").
    """
    cached = cache.get_whoami(user_oid)
    if cached is not None:
        logger.debug("WhoAmI served from cache (UserId=%s)", cached.get("UserId"))
        return cached

    logger.debug("WhoAmI cache miss — fetching from API")
    result = await _request("GET", "/WhoAmI", token=token)
    user_id = result.get("UserId")
    data = {
        "UserId": user_id,
    }

    # Fetch the user's display name from systemusers
    if user_id:
        try:
            user = await _request(
                "GET",
                f"/systemusers({user_id})",
                token=token,
                params={"$select": "fullname"},
            )
            data["FullName"] = user.get("fullname")
        except Exception as e:
            logger.warning("Failed to fetch user fullname: %s", e)

        # Fetch the user's timezone from usersettings + timezonedefinitions
        try:
            tz_settings = await _request(
                "GET",
                f"/usersettingscollection({user_id})",
                token=token,
                params={"$select": "timezonecode"},
            )
            tz_code = tz_settings.get("timezonecode")
            if tz_code is not None:
                data["TimeZoneCode"] = tz_code
                tz_defs = await _request(
                    "GET",
                    "/timezonedefinitions",
                    token=token,
                    params={
                        "$filter": f"timezonecode eq {tz_code}",
                        "$select": "standardname,userinterfacename",
                    },
                )
                tz_values = tz_defs.get("value", [])
                if tz_values:
                    data["TimeZoneName"] = tz_values[0].get("standardname")
        except Exception as e:
            logger.warning("Failed to fetch user timezone: %s", e)

    cache.set_whoami(user_oid, data)
    return data


async def list_records(
    table: str,
    token: str,
    filter_expr: Optional[str] = None,
    select: Optional[str] = None,
    top: int = 50,
    orderby: Optional[str] = None,
    fetch_all_pages: bool = False,
) -> list[dict]:
    """
    Query records from a Dataverse table using OData query options.

    Follows @odata.nextLink for paginated results up to MAX_PAGES pages.
    Records are never cached — always fetched live from the API.
    """
    params: dict[str, Any] = {"$top": min(top, 5000)}
    if filter_expr:
        params["$filter"] = filter_expr
    if select:
        params["$select"] = select
    if orderby:
        params["$orderby"] = orderby

    all_records: list[dict] = []
    path = f"/{table}"
    pages = 0

    while path and pages < MAX_PAGES:
        result = await _request("GET", path, token=token, params=params if pages == 0 else None)
        records = result.get("value", [])
        all_records.extend(records)
        pages += 1

        next_link = result.get("@odata.nextLink")
        if next_link and fetch_all_pages:
            path = next_link.replace(settings.api_base, "")
        else:
            break

    return all_records


async def create_record(table: str, data: dict, token: str) -> str:
    """
    Create a new record in the specified Dataverse table.

    Returns the primary ID (GUID) of the created record, extracted from the
    OData-EntityId response header. No Prefer: return=representation is used,
    so Dataverse returns 204 No Content (saves tokens).
    """
    headers = _get_headers(token)
    url = f"{settings.api_base}/{table}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=data)

    if not response.is_success:
        error_msg = _parse_dataverse_error(response)
        logger.error("Dataverse API error POST %s: %s", url, error_msg)
        raise httpx.HTTPStatusError(
            error_msg,
            request=response.request,
            response=response,
        )

    # Extract GUID from OData-EntityId header: https://org.crm.dynamics.com/api/data/v9.2/accounts(guid)
    entity_id = response.headers.get("OData-EntityId", "")
    guid = entity_id.rsplit("(", 1)[-1].rstrip(")") if "(" in entity_id else ""
    return guid


async def update_record(table: str, record_id: str, data: dict, token: str) -> None:
    """
    Update an existing record using PATCH (partial update — only provided fields are changed).

    record_id must be the GUID value of the record's primary key (without braces).
    """
    await _request("PATCH", f"/{table}({record_id})", token=token, json=data)


async def delete_record(table: str, record_id: str, token: str) -> None:
    """
    Permanently delete a record from Dataverse.

    record_id must be the GUID value of the record's primary key (without braces).
    This action is irreversible.
    """
    await _request("DELETE", f"/{table}({record_id})", token=token)


async def list_tables(token: str) -> str:
    """
    Return a compact pipe-delimited list of all Dataverse tables.

    Results are cached for 24 hours. Output contains LogicalName, DisplayName,
    and EntitySetName — enough to look up the correct table name before calling
    get_table_schema() for field details.
    """
    cached = cache.get_tables()
    if cached is not None:
        logger.debug("Tables list served from cache")
        return cached

    logger.debug("Tables list cache miss — fetching from API")
    result = await _request(
        "GET",
        "/EntityDefinitions",
        token=token,
        params={
            "$select": "LogicalName,DisplayName,EntitySetName",
            "$filter": "IsIntersect eq false and IsPrivate eq false",
        },
    )

    def label(obj) -> Optional[str]:
        if not obj:
            return None
        lv = obj.get("LocalizedLabels", [])
        return lv[0].get("Label") if lv else (obj.get("UserLocalizedLabel") or {}).get("Label")

    lines = ["LogicalName | DisplayName | EntitySetName"]
    for e in result.get("value", []):
        ln = e.get("LogicalName", "")
        dn = label(e.get("DisplayName")) or ""
        es = e.get("EntitySetName", "")
        if not dn or _is_system_table(ln):
            continue
        lines.append(f"{ln} | {dn} | {es}")

    text = "\n".join(lines)
    cache.set_tables(text)
    return text


async def get_table_schema(token: str, table_names: Optional[list[str]] = None) -> str:
    """
    Retrieve entity definitions (schema) from Dataverse metadata.

    Returns compact pipe-delimited text. When specific table names are provided,
    each table is checked against the schema cache (TTL: 1 hour) before making
    an API call. Multiple tables are separated by "---".
    """
    if table_names:
        results = []
        for name in table_names:
            # Check cache first
            cached = cache.get_schema(name)
            if cached is not None:
                logger.debug("Schema cache hit for '%s'", name)
                results.append(cached)
                continue

            # Cache miss — fetch from API
            logger.debug("Schema cache miss for '%s' — fetching from API", name)
            entity = await _request(
                "GET",
                f"/EntityDefinitions(LogicalName='{name}')",
                token=token,
                params={"$select": "LogicalName,DisplayName,PrimaryIdAttribute,PrimaryNameAttribute"},
            )
            attrs_result = await _request(
                "GET",
                f"/EntityDefinitions(LogicalName='{name}')/Attributes",
                token=token,
                params={
                    "$select": "LogicalName,DisplayName,AttributeType,RequiredLevel,Description",
                    "$filter": "AttributeType ne 'Virtual'",
                },
            )
            entity["Attributes"] = attrs_result.get("value", [])
            cleaned = _clean_entity(entity)
            cache.set_schema(name, cleaned)
            results.append(cleaned)

        return "\n\n---\n\n".join(results)
    else:
        # Full table list — not cached (it's a cheap metadata-only query)
        result = await _request(
            "GET",
            "/EntityDefinitions",
            token=token,
            params={"$select": "LogicalName,DisplayName,PrimaryIdAttribute,PrimaryNameAttribute"},
        )
        return "\n\n---\n\n".join(_clean_entity(e) for e in result.get("value", []))


def _clean_entity(entity: dict) -> str:
    """Format entity metadata as compact pipe-delimited text."""
    def label(obj) -> Optional[str]:
        if not obj:
            return None
        lv = obj.get("LocalizedLabels", [])
        return lv[0].get("Label") if lv else (obj.get("UserLocalizedLabel") or {}).get("Label")

    name = entity.get("LogicalName", "")
    display = label(entity.get("DisplayName")) or ""
    pid = entity.get("PrimaryIdAttribute", "")
    pname = entity.get("PrimaryNameAttribute", "")

    lines = [
        f"Table: {name} ({display})",
        f"Primary ID: {pid}",
        f"Primary Name: {pname}",
        "",
        "Field | Display Name | Type | Req",
    ]

    for a in entity.get("Attributes", []):
        ln = a.get("LogicalName", "")
        if ln in SYSTEM_ATTRIBUTES:
            continue
        display_name = label(a.get("DisplayName")) or ""
        atype = a.get("AttributeType", "")
        req_val = a.get("RequiredLevel", {}).get("Value", "")
        req = "Y" if req_val in ("SystemRequired", "ApplicationRequired") else ""
        desc = label(a.get("Description")) or ""
        line = f"{ln} | {display_name} | {atype} | {req}"
        if desc:
            line += f" — {desc}"
        lines.append(line)

    return "\n".join(lines)
