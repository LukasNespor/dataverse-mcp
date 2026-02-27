"""
Shared test fixtures and environment setup.

Environment variables are set before any src module is imported so that
pydantic-settings ``Settings`` can be instantiated without a .env file.

Heavy optional dependencies (msal, fastmcp, httpx) are stubbed out so that
unit tests can run without installing the full production stack. Redis is
provided via the ``fakeredis`` package.
"""

import os
import sys
import types

# Inject required env vars before config.Settings is instantiated.
os.environ.setdefault("DATAVERSE_URL", "https://test.crm.dynamics.com")
os.environ.setdefault("CLIENT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Ensure src/ is on the import path.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Stub out heavy dependencies that aren't needed for unit tests
# ---------------------------------------------------------------------------

def _ensure_stub(name: str) -> None:
    """Register a dummy module if *name* is not already importable."""
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)


# msal — used by auth.py (module-level: SerializableTokenCache, PublicClientApplication)
_msal = types.ModuleType("msal")
_msal.SerializableTokenCache = type("SerializableTokenCache", (), {
    "deserialize": lambda self, s: None,
    "serialize": lambda self: "{}",
    "has_state_changed": False,
})
_msal.PublicClientApplication = type("PublicClientApplication", (), {})
sys.modules.setdefault("msal", _msal)

# httpx — used by dataverse.py
_httpx = types.ModuleType("httpx")

class _FakeHTTPStatusError(Exception):
    pass

_httpx.HTTPStatusError = _FakeHTTPStatusError
_httpx.AsyncClient = None
_httpx.Response = None
sys.modules.setdefault("httpx", _httpx)

# fastmcp — used by main.py / token_resolver.py
for mod in [
    "fastmcp",
    "fastmcp.server",
    "fastmcp.server.auth",
    "fastmcp.server.auth.providers",
    "fastmcp.server.auth.providers.azure",
]:
    _ensure_stub(mod)


# ---------------------------------------------------------------------------
# Patch Redis to use fakeredis before any src module connects
# ---------------------------------------------------------------------------

import fakeredis
import redis as _real_redis_module

# Replace Redis.from_url so that cache.py / confirmations.py get a fakeredis
# instance instead of trying to connect to a real server.
_fake_server = fakeredis.FakeServer()


def _fake_from_url(url, **kwargs):
    return fakeredis.FakeRedis(server=_fake_server, decode_responses=kwargs.get("decode_responses", False))


_real_redis_module.Redis.from_url = staticmethod(_fake_from_url)


# ---------------------------------------------------------------------------
# Per-test isolation: flush fakeredis between tests
# ---------------------------------------------------------------------------

import pytest

@pytest.fixture(autouse=True)
def _flush_fakeredis():
    """Flush all fakeredis data before each test for isolation."""
    _fake_server.connected = True
    # Flush via a temporary client
    r = fakeredis.FakeRedis(server=_fake_server)
    r.flushall()
    yield
    r.flushall()
