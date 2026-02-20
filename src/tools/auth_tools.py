"""
Authentication tools exposed via MCP.

These tools manage the interactive browser authentication flow.
The agent should call `authenticate` if any other tool returns an
authentication error, then proceed with Dataverse tools once the
user confirms they have signed in.
"""

import logging

import cache
from auth import (
    AuthenticationRequiredError,
    sign_out,
    start_interactive_auth,
)
from config import settings

logger = logging.getLogger(__name__)


async def tool_authenticate() -> str:
    """
    Start a Microsoft interactive browser authentication flow and return instructions for the user.

    Call this tool when:
    - Any other tool returns an error mentioning authentication
    - The user says they are not logged in or asks to log in
    - You are starting a session and do not know if the user is authenticated

    This tool starts a local redirect server and returns a URL that the user
    must open in their browser to sign in. The token exchange happens automatically
    when the browser redirects back — no second tool call is needed.

    After the user confirms they have signed in, you can proceed to call
    Dataverse (CRM) tools directly.

    Returns a message containing the sign-in URL.
    """
    try:
        auth_url = start_interactive_auth()
        port = settings.auth_redirect_port
        return (
            f"IMPORTANT: You MUST include the full sign-in URL below in your response "
            f"so the user can click it. Do NOT say 'open the link above' — the user "
            f"cannot see tool call outputs directly.\n\n"
            f"Sign-in URL: {auth_url}\n\n"
            f"Ask the user to open this URL in their browser, sign in with their "
            f"Microsoft account, and let you know once they are done."
        )
    except Exception as e:
        logger.exception("Failed to start interactive authentication")
        return f"Failed to initiate authentication: {e}"


async def tool_sign_out() -> str:
    """
    Sign out the current user from Dataverse (CRM).

    This tool deletes the cached authentication token and clears the cached user
    identity (WhoAmI). After signing out, the user can sign in again with a
    different account using the `Sign in to Dataverse` tool.

    Call this tool when:
    - The user wants to log out or sign out
    - The user wants to switch to a different Dataverse account

    Returns a confirmation message.
    """
    try:
        sign_out()
        cache.invalidate_whoami()
        return (
            "Successfully signed out. Token cache and user identity have been cleared.\n\n"
            "To sign in with a different account, call `Sign_in_to_Dataverse`."
        )
    except Exception as e:
        logger.exception("Failed to sign out")
        return f"Failed to sign out: {e}"


