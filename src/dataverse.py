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
from auth import get_token, AuthenticationRequiredError
from config import settings

logger = logging.getLogger(__name__)

# Maximum number of pages to follow when paginating list results
MAX_PAGES = 20


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


async def _get_headers() -> dict[str, str]:
    """Build authorization headers with a fresh access token."""
    token = await get_token()
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
    params: Optional[dict] = None,
    json: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
) -> Optional[Any]:
    """
    Execute an authenticated HTTP request against the Dataverse Web API.

    Raises:
        AuthenticationRequiredError: if no valid token exists.
        httpx.HTTPStatusError: on 4xx/5xx responses (with Dataverse error message).
        httpx.RequestError: on network-level failures.
    """
    headers = await _get_headers()
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


async def whoami() -> dict:
    """
    Return the identity of the currently authenticated Dataverse user.

    Results are cached for 24 hours. The cache is invalidated automatically when
    the user re-authenticates. API is only called on first use, after cache
    expiry, or after explicit invalidation.

    Returns:
      - UserId (str): GUID of the authenticated user — use for owner/assignee fields.
      - BusinessUnitId (str): GUID of the user's business unit.
      - OrganizationId (str): GUID of the Dataverse organization.
      - FullName (str): Display name of the authenticated user.
    """
    cached = cache.get_whoami()
    if cached is not None:
        logger.debug("WhoAmI served from cache (UserId=%s)", cached.get("UserId"))
        return cached

    logger.debug("WhoAmI cache miss — fetching from API")
    result = await _request("GET", "/WhoAmI")
    user_id = result.get("UserId")
    data = {
        "UserId": user_id,
        "BusinessUnitId": result.get("BusinessUnitId"),
        "OrganizationId": result.get("OrganizationId"),
    }

    # Fetch the user's display name from systemusers
    if user_id:
        try:
            user = await _request(
                "GET",
                f"/systemusers({user_id})",
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
                params={"$select": "timezonecode"},
            )
            tz_code = tz_settings.get("timezonecode")
            if tz_code is not None:
                data["TimeZoneCode"] = tz_code
                tz_defs = await _request(
                    "GET",
                    "/timezonedefinitions",
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

    cache.set_whoami(data)
    return data


async def list_records(
    table: str,
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
        result = await _request("GET", path, params=params if pages == 0 else None)
        records = result.get("value", [])
        all_records.extend(records)
        pages += 1

        next_link = result.get("@odata.nextLink")
        if next_link and fetch_all_pages:
            path = next_link.replace(settings.api_base, "")
        else:
            break

    return all_records


async def create_record(table: str, data: dict) -> dict:
    """
    Create a new record in the specified Dataverse table.

    Returns the full created record including its primary ID field.
    Uses Prefer: return=representation to get the record back in one round-trip.
    """
    return await _request(
        "POST",
        f"/{table}",
        json=data,
        extra_headers={"Prefer": "return=representation"},
    )


async def update_record(table: str, record_id: str, data: dict) -> None:
    """
    Update an existing record using PATCH (partial update — only provided fields are changed).

    record_id must be the GUID value of the record's primary key (without braces).
    """
    await _request("PATCH", f"/{table}({record_id})", json=data)


async def delete_record(table: str, record_id: str) -> None:
    """
    Permanently delete a record from Dataverse.

    record_id must be the GUID value of the record's primary key (without braces).
    This action is irreversible.
    """
    await _request("DELETE", f"/{table}({record_id})")


async def list_tables() -> list[dict]:
    """
    Return a lightweight list of all Dataverse tables with basic identifiers.

    Results are cached for 24 hours. Each entry contains LogicalName,
    DisplayName, EntitySetName, and IsCustomEntity — enough to look up the
    correct table name before calling get_table_schema() for field details.
    """
    cached = cache.get_tables()
    if cached is not None:
        logger.debug("Tables list served from cache (%d tables)", len(cached))
        return cached

    logger.debug("Tables list cache miss — fetching from API")
    result = await _request(
        "GET",
        "/EntityDefinitions",
        params={"$select": "LogicalName,DisplayName,EntitySetName,IsCustomEntity"},
    )

    def label(obj) -> Optional[str]:
        if not obj:
            return None
        lv = obj.get("LocalizedLabels", [])
        return lv[0].get("Label") if lv else (obj.get("UserLocalizedLabel") or {}).get("Label")

    cleaned = [
        {
            "LogicalName": e.get("LogicalName"),
            "DisplayName": label(e.get("DisplayName")),
            "EntitySetName": e.get("EntitySetName"),
            "IsCustomEntity": e.get("IsCustomEntity"),
        }
        for e in result.get("value", [])
    ]

    cache.set_tables(cleaned)
    return cleaned


async def get_table_schema(table_names: Optional[list[str]] = None) -> list[dict]:
    """
    Retrieve entity definitions (schema) from Dataverse metadata.

    When specific table names are provided, each table is checked against the
    schema cache (TTL: 1 hour) before making an API call. Only tables not found
    in cache (or with an expired cache entry) trigger an API request.

    If table_names is None or empty, returns the list of all entity definitions
    without attributes (no caching applied — this is a lightweight index query).

    Each returned entity includes LogicalName, DisplayName, PrimaryIdAttribute,
    PrimaryNameAttribute, and Attributes (when specific tables are requested).
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
                params={"$select": "LogicalName,DisplayName,PrimaryIdAttribute,PrimaryNameAttribute"},
            )
            attrs_result = await _request(
                "GET",
                f"/EntityDefinitions(LogicalName='{name}')/Attributes",
                params={
                    "$select": "LogicalName,DisplayName,AttributeType,RequiredLevel,Description",
                    "$filter": "AttributeType ne 'Virtual'",
                },
            )
            entity["Attributes"] = attrs_result.get("value", [])
            cleaned = _clean_entity(entity)
            cache.set_schema(name, cleaned)
            results.append(cleaned)

        return results
    else:
        # Full table list — not cached (it's a cheap metadata-only query)
        result = await _request(
            "GET",
            "/EntityDefinitions",
            params={"$select": "LogicalName,DisplayName,PrimaryIdAttribute,PrimaryNameAttribute"},
        )
        return [_clean_entity(e) for e in result.get("value", [])]


def _clean_entity(entity: dict) -> dict:
    """Normalize entity metadata into a clean, consistent structure."""
    def label(obj) -> Optional[str]:
        if not obj:
            return None
        lv = obj.get("LocalizedLabels", [])
        return lv[0].get("Label") if lv else (obj.get("UserLocalizedLabel") or {}).get("Label")

    def clean_attr(a: dict) -> dict:
        return {
            "LogicalName": a.get("LogicalName"),
            "DisplayName": label(a.get("DisplayName")),
            "AttributeType": a.get("AttributeType"),
            "RequiredLevel": a.get("RequiredLevel", {}).get("Value"),
            "Description": label(a.get("Description")),
        }

    cleaned = {
        "LogicalName": entity.get("LogicalName"),
        "DisplayName": label(entity.get("DisplayName")),
        "PrimaryIdAttribute": entity.get("PrimaryIdAttribute"),
        "PrimaryNameAttribute": entity.get("PrimaryNameAttribute"),
    }
    if "Attributes" in entity:
        cleaned["Attributes"] = [clean_attr(a) for a in entity["Attributes"]]
    return cleaned
