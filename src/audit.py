"""
Structured audit logging for Dataverse MCP tool invocations.

All audit entries go to the ``dataverse.audit`` logger at INFO level so they
can be captured, filtered, or forwarded independently of application logs.
"""

import functools
import json
import logging
import time
from typing import Any, Callable, Optional

audit_logger = logging.getLogger("dataverse.audit")


def _emit(event: dict[str, Any]) -> None:
    """Write a single JSON audit record."""
    audit_logger.info(json.dumps(event, default=str))


def log_tool_call(
    tool_name: str,
    category: str,
    status: str,
    *,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
    **extra: Any,
) -> None:
    """Log a generic tool invocation."""
    event: dict[str, Any] = {
        "event": "tool_call",
        "tool": tool_name,
        "category": category,
        "status": status,
        "ts": time.time(),
    }
    if user_id:
        event["user_id"] = user_id
    if user_name:
        event["user_name"] = user_name
    if correlation_id:
        event["correlation_id"] = correlation_id
    if extra:
        event["details"] = extra
    _emit(event)


def log_proposal(
    proposal_id: str,
    impact_summary: str,
    ttl: int,
    *,
    token_fingerprint: str,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> None:
    """Log the creation of a delete proposal.

    *token_fingerprint* should be the first 8 characters of the token hash —
    never the plaintext token.
    """
    _emit({
        "event": "delete_proposed",
        "proposal_id": proposal_id,
        "impact": impact_summary,
        "ttl_seconds": ttl,
        "token_fingerprint": token_fingerprint,
        "user_id": user_id,
        "user_name": user_name,
        "ts": time.time(),
    })


def log_confirm(
    proposal_id: str,
    success: bool,
    *,
    reason: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
) -> None:
    """Log the outcome of a delete confirmation attempt."""
    event: dict[str, Any] = {
        "event": "delete_confirmed" if success else "delete_confirm_failed",
        "proposal_id": proposal_id,
        "success": success,
        "ts": time.time(),
    }
    if reason:
        event["reason"] = reason
    if user_id:
        event["user_id"] = user_id
    if user_name:
        event["user_name"] = user_name
    _emit(event)


def audited_tool(tool_name: str, category: str) -> Callable:
    """Decorator that wraps an async tool function with audit logging.

    Emits a ``log_tool_call`` entry after every invocation with status
    ``ok`` or ``error``.  The ``_obo_token`` kwarg (if present) is used
    to resolve user context for the audit record.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            obo_token = kwargs.get("_obo_token")
            user_id, user_name = get_user_context(obo_token)
            try:
                result = await fn(*args, **kwargs)
                log_tool_call(
                    tool_name, category, "ok",
                    user_id=user_id, user_name=user_name,
                )
                return result
            except Exception:
                log_tool_call(
                    tool_name, category, "error",
                    user_id=user_id, user_name=user_name,
                )
                raise
        return wrapper
    return decorator


def get_user_context(obo_token: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of (user_id, user_name) from the cached WhoAmI.

    Returns ("unknown", "unknown") when identity cannot be determined.
    """
    try:
        import cache
        from token_resolver import get_user_oid
        user_oid = get_user_oid(obo_token)
        whoami = cache.get_whoami(user_oid)
        if whoami:
            return whoami.get("UserId"), whoami.get("FullName")
    except Exception:
        pass
    return "unknown", "unknown"
