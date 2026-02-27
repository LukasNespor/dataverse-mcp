"""
Token resolver for dual-mode operation (Azure OBO vs local MSAL).

In Azure mode, the token is provided by FastMCP's EntraOBOToken dependency
injection. In local mode, the token is acquired via the MSAL interactive flow.

This module provides:
  - OBO_TOKEN_DEFAULT: use as the default value for `_obo_token` parameters
    in tool functions. In Azure mode it's an EntraOBOToken marker that FastMCP
    replaces with the actual OBO token. In local mode it's None.
  - resolve_token(): resolves the token to a string in both modes.
  - get_user_oid(): extracts the user OID from a JWT token (Azure mode only).
"""

import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

if settings.is_azure_mode:
    from fastmcp.server.auth.providers.azure import EntraOBOToken
    OBO_TOKEN_DEFAULT = EntraOBOToken([f"{settings.dataverse_url}/user_impersonation"])
else:
    OBO_TOKEN_DEFAULT = None


async def resolve_token(obo_token: Optional[str] = None) -> str:
    """
    Return a valid Dataverse Bearer token.

    Args:
        obo_token: Token provided by FastMCP EntraOBOToken in Azure mode.
                   None in local mode.

    Returns:
        A valid access token string.

    Raises:
        auth.AuthenticationRequiredError: in local mode when no cached token exists.
    """
    if obo_token:
        return obo_token
    # Local mode — use MSAL interactive auth
    from auth import get_token
    return await get_token()


def get_user_oid(obo_token: Optional[str] = None) -> Optional[str]:
    """
    Extract the user's Entra ID object ID (oid) from an OBO token.

    Returns None in local mode (single-user, no per-user keying needed).
    """
    if not obo_token or not settings.is_azure_mode:
        return None

    import json
    import base64

    try:
        # JWT payload is the second segment
        payload = obo_token.split(".")[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("oid")
    except Exception:
        return None
