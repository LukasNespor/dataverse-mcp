"""
Token resolver for Azure OBO mode.

The token is provided by FastMCP's EntraOBOToken dependency injection.

This module provides:
  - OBO_TOKEN_DEFAULT: use as the default value for `_obo_token` parameters
    in tool functions. It's an EntraOBOToken marker that FastMCP replaces
    with the actual OBO token.
  - resolve_token(): resolves the token to a string.
  - get_user_oid(): extracts the user OID from a JWT token.
"""

import json
import base64
import logging
from typing import Optional

from config import settings
from fastmcp.server.auth.providers.azure import EntraOBOToken

logger = logging.getLogger(__name__)

OBO_TOKEN_DEFAULT = EntraOBOToken([f"{settings.dataverse_url}/user_impersonation"])


async def resolve_token(obo_token: Optional[str] = None) -> str:
    """
    Return a valid Dataverse Bearer token.

    Args:
        obo_token: Token provided by FastMCP EntraOBOToken.

    Returns:
        A valid access token string.

    Raises:
        RuntimeError: when the injected token is not a string
                      (indicates a FastMCP dependency injection failure).
    """
    if obo_token is not None and isinstance(obo_token, str):
        return obo_token

    raise RuntimeError(
        f"resolve_token: expected a string OBO token but received "
        f"{type(obo_token).__name__ if obo_token is not None else 'None'}. "
        "This indicates a FastMCP dependency injection failure — the EntraOBOToken "
        "dependency was not resolved before the tool was called."
    )


def get_user_oid(obo_token: Optional[str] = None) -> Optional[str]:
    """
    Extract a unique user identifier from an OBO token for per-user cache keying.

    Tries the following claims in order:
    - oid (Entra ID object ID — stable, preferred)
    - sub (subject — resource-specific but always present as fallback)

    Returns None if the token cannot be decoded.
    """
    if not obo_token or not isinstance(obo_token, str):
        logger.warning(
            "get_user_oid: obo_token is missing or not a string (type=%s). "
            "Per-user cache keying disabled — falling back to global key.",
            type(obo_token).__name__,
        )
        return None

    try:
        # JWT payload is the second segment (header.payload.signature)
        payload = obo_token.split(".")[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))

        oid = claims.get("oid")
        if oid:
            logger.debug("get_user_oid: resolved oid=%s", oid)
            return oid

        # Fallback: sub is always present in Entra ID tokens
        sub = claims.get("sub")
        if sub:
            logger.warning(
                "get_user_oid: 'oid' claim missing from OBO token — falling back to 'sub'. "
                "Claims present: %s",
                list(claims.keys()),
            )
            return sub

        logger.error(
            "get_user_oid: neither 'oid' nor 'sub' found in OBO token. "
            "Claims present: %s. Per-user cache keying disabled.",
            list(claims.keys()),
        )
        return None
    except Exception as exc:
        logger.error("get_user_oid: failed to decode OBO token: %s", exc)
        return None
