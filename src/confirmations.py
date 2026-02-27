"""
Proposal store for two-step destructive operations.

Delete operations go through a propose → confirm workflow:
  1. The propose step creates a Proposal with a one-time confirm token.
  2. The confirm step validates the token, phrase, and expiry before executing.

Proposals are stored in Redis with automatic TTL-based key expiry. The ``used``
flag is set atomically via a Lua CAS script to prevent replay across replicas.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from config import settings

logger = logging.getLogger(__name__)

# Fixed confirmation phrase the caller must echo back.
CONFIRM_PHRASE = "CONFIRM DELETE"

# Redis key prefix to namespace proposals.
_REDIS_PREFIX = "mcp:proposal:"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfirmationError(Exception):
    """Base class for confirmation-related errors."""


class ProposalNotFoundError(ConfirmationError):
    """Raised when the proposal_id does not match any stored proposal."""


class ProposalExpiredError(ConfirmationError):
    """Raised when the proposal has exceeded its TTL."""


class ProposalAlreadyUsedError(ConfirmationError):
    """Raised on replay — the proposal was already consumed."""


class ConfirmPhraseMismatchError(ConfirmationError):
    """Raised when the supplied confirm phrase does not match."""


class TokenMismatchError(ConfirmationError):
    """Raised when the supplied confirm token does not match the stored hash."""


# ---------------------------------------------------------------------------
# Proposal dataclass
# ---------------------------------------------------------------------------

@dataclass
class Proposal:
    proposal_id: str
    table: str
    record_id: str
    token_hash: str  # SHA-256 hex digest of the confirm token
    confirm_phrase: str
    created_at: float = field(default_factory=time.time)
    used: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------

# Lua script: set used=true only if currently false, return old value.
# Returns 0 (was unused, now marked) or 1 (already used).
_CAS_SCRIPT = """
local val = redis.call('HGET', KEYS[1], 'used')
if val == '1' then
    return 1
end
redis.call('HSET', KEYS[1], 'used', '1')
return 0
"""


def _connect_redis():
    import redis as redis_lib
    r = redis_lib.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
    )
    r.ping()
    logger.info("Redis proposal store connected to %s", settings.redis_url.split("@")[-1])
    return r


_redis = _connect_redis()
_cas_sha: str | None = None


def _key(proposal_id: str) -> str:
    return f"{_REDIS_PREFIX}{proposal_id}"


def _put(proposal: Proposal) -> None:
    data = asdict(proposal)
    data["used"] = "1" if proposal.used else "0"
    data["created_at"] = str(proposal.created_at)
    key = _key(proposal.proposal_id)
    pipe = _redis.pipeline()
    pipe.hset(key, mapping=data)
    pipe.expire(key, settings.confirm_token_ttl_seconds)
    pipe.execute()


def _get(proposal_id: str) -> Proposal | None:
    data = _redis.hgetall(_key(proposal_id))
    if not data:
        return None
    return Proposal(
        proposal_id=data["proposal_id"],
        table=data["table"],
        record_id=data["record_id"],
        token_hash=data["token_hash"],
        confirm_phrase=data["confirm_phrase"],
        created_at=float(data["created_at"]),
        used=data["used"] == "1",
    )


def _mark_used(proposal_id: str) -> None:
    """Atomically mark proposal as used. Raises ProposalAlreadyUsedError on race."""
    global _cas_sha
    key = _key(proposal_id)
    if _cas_sha is None:
        _cas_sha = _redis.script_load(_CAS_SCRIPT)
    result = _redis.evalsha(_cas_sha, 1, key)
    if result == 1:
        raise ProposalAlreadyUsedError(
            f"Proposal '{proposal_id}' has already been used. "
            "Each delete proposal can only be confirmed once."
        )


def _delete(proposal_id: str) -> None:
    _redis.delete(_key(proposal_id))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_proposal(table: str, record_id: str) -> tuple[str, str, str]:
    """Create a new delete proposal.

    Returns:
        (proposal_id, confirm_token, confirm_phrase)

    The confirm_token is returned in plaintext exactly once — only its
    SHA-256 hash is stored.
    """
    proposal_id = str(uuid4())
    confirm_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(confirm_token)

    _put(Proposal(
        proposal_id=proposal_id,
        table=table,
        record_id=record_id,
        token_hash=token_hash,
        confirm_phrase=CONFIRM_PHRASE,
    ))

    logger.info(
        "Delete proposal created: id=%s table=%s record=%s",
        proposal_id, table, record_id,
    )
    return proposal_id, confirm_token, CONFIRM_PHRASE


def validate_and_consume(
    proposal_id: str,
    confirm_token: str,
    confirm_phrase: str,
) -> Proposal:
    """Validate and consume a proposal, returning it on success.

    Raises a ConfirmationError subclass on any validation failure.
    """
    proposal = _get(proposal_id)
    if proposal is None:
        raise ProposalNotFoundError(
            f"No proposal found with id '{proposal_id}'. "
            "It may have expired or never existed."
        )

    # Check expiry (belt-and-suspenders — Redis TTL handles this too)
    ttl = settings.confirm_token_ttl_seconds
    age = time.time() - proposal.created_at
    if age > ttl:
        _delete(proposal_id)
        raise ProposalExpiredError(
            f"Proposal '{proposal_id}' expired ({age:.0f}s > {ttl}s TTL). "
            "Please create a new delete request."
        )

    # Replay protection — atomic via Lua CAS
    _mark_used(proposal_id)  # raises ProposalAlreadyUsedError on race

    # Phrase check
    if confirm_phrase != proposal.confirm_phrase:
        raise ConfirmPhraseMismatchError(
            f"Confirmation phrase mismatch. Expected '{proposal.confirm_phrase}', "
            f"got '{confirm_phrase}'."
        )

    # Token check
    if _hash_token(confirm_token) != proposal.token_hash:
        raise TokenMismatchError(
            "Confirmation token does not match. "
            "Use the exact token returned by the propose step."
        )

    logger.info("Delete proposal confirmed: id=%s", proposal_id)
    return proposal
