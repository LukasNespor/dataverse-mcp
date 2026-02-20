"""
Authentication module for Microsoft Dataverse MCP Server.

Handles authentication via interactive browser flow, token caching, and silent
token refresh using the Microsoft Authentication Library (MSAL).

Token cache is persisted as a JSON file on disk with restrictive file
permissions (chmod 600). The Docker volume and non-root container user
provide the security boundary.
"""

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import msal

from config import settings

logger = logging.getLogger(__name__)

_token_cache = msal.SerializableTokenCache()
_app: Optional[msal.PublicClientApplication] = None
_auth_lock = asyncio.Lock()


def _load_cache() -> None:
    """
    Load the token cache from disk.

    On read errors the cache is left empty and an error is logged —
    the user will be prompted to re-authenticate.
    """
    cache_path = Path(settings.token_cache_path)
    if not cache_path.exists():
        logger.info("No existing token cache found at %s, starting fresh", cache_path)
        return

    try:
        data = cache_path.read_text(encoding="utf-8")
        _token_cache.deserialize(data)
        logger.info("Token cache loaded from %s", cache_path)
    except Exception as e:
        logger.warning("Failed to load token cache, starting fresh: %s", e)


def _save_cache() -> None:
    """
    Persist the token cache to disk if it has changed.

    File is written atomically via a temp file + rename to prevent partial
    writes leaving a corrupt cache. Permissions are set to 600 (owner rw only).
    """
    if not _token_cache.has_state_changed:
        return

    cache_path = Path(settings.token_cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")

    try:
        tmp_path.write_text(_token_cache.serialize(), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.rename(cache_path)
        logger.info("Token cache saved to %s", cache_path)
    except Exception as e:
        logger.error("Failed to save token cache: %s", e)
        tmp_path.unlink(missing_ok=True)


def _get_app() -> msal.PublicClientApplication:
    """Return the MSAL PublicClientApplication, creating it on first call."""
    global _app
    if _app is None:
        _load_cache()
        _app = msal.PublicClientApplication(
            client_id=settings.client_id,
            authority=settings.authority,
            token_cache=_token_cache,
        )
    return _app


async def get_token() -> str:
    """
    Return a valid Bearer access token for Dataverse.

    Attempts silent acquisition first (uses cached access token or automatically
    uses the refresh token if the access token is expired — MSAL handles this).
    If no valid session exists at all, raises AuthenticationRequiredError.

    Thread-safe via asyncio.Lock to prevent concurrent refresh races.
    """
    async with _auth_lock:
        app = _get_app()
        accounts = app.get_accounts()

        if accounts:
            result = app.acquire_token_silent(
                scopes=settings.scopes,
                account=accounts[0],
            )
            if result and "access_token" in result:
                _save_cache()
                return result["access_token"]

        raise AuthenticationRequiredError(
            "No valid token found. Call the `Sign in to Dataverse` tool to sign in."
        )



def start_interactive_auth() -> str:
    """
    Build the authorization URL and start the local redirect server immediately.

    The HTTP server is started in a background thread BEFORE returning the URL,
    so it is already listening when the browser redirects after sign-in.
    The background thread also exchanges the authorization code for tokens
    and saves the cache — no second tool call is needed.

    Returns the authorization URL that the user must open in their browser.
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse, parse_qs

    app = _get_app()
    port = settings.auth_redirect_port
    redirect_uri = f"http://localhost:{port}"

    flow = app.initiate_auth_code_flow(
        scopes=settings.scopes,
        redirect_uri=redirect_uri,
    )
    auth_url = flow["auth_uri"]

    server_ready = threading.Event()

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            # Flatten query params to single values for MSAL
            auth_response = {k: v[0] for k, v in params.items()}

            result = app.acquire_token_by_auth_code_flow(flow, auth_response)

            if "access_token" in result:
                _save_cache()
                logger.info("Interactive browser authentication successful, token cache updated")
                self._respond("Authentication complete. You can close this tab.")
            else:
                error_msg = result.get("error_description", result.get("error", "Unknown error"))
                logger.error("Token exchange failed: %s", error_msg)
                self._respond(f"Authentication failed: {error_msg}")

        def _respond(self, message: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h3>{message}</h3></body></html>".encode())

        def log_message(self, format, *args):
            logger.debug("Auth redirect server: %s", format % args)

    def run_server():
        server = HTTPServer(("0.0.0.0", port), RedirectHandler)
        server.timeout = 300  # 5 minute timeout
        server_ready.set()
        logger.info("Auth redirect server listening on port %d", port)
        server.handle_request()
        server.server_close()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    server_ready.wait(timeout=5)

    return auth_url


def sign_out() -> None:
    """
    Clear all cached tokens — both in-memory (MSAL) and the file on disk.

    After calling this the user must re-authenticate via `Sign in to Dataverse`,
    which allows them to choose a different account.
    """
    global _app

    # 1. Clear the in-memory MSAL token cache
    _token_cache.deserialize("{}")
    _token_cache.has_state_changed = False

    # 2. Drop the cached MSAL app so it is recreated on next auth
    _app = None

    # 3. Delete the token cache file from disk
    cache_path = Path(settings.token_cache_path)
    if cache_path.exists():
        cache_path.unlink()
        logger.info("Token cache file deleted: %s", cache_path)
    else:
        logger.info("No token cache file to delete at %s", cache_path)


class AuthenticationRequiredError(Exception):
    """Raised when no valid token is available and authentication must be initiated."""
    pass
