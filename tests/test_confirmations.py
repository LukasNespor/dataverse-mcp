"""Tests for src/confirmations.py — Redis-backed proposal store and two-step confirm logic."""

import hashlib
import time

import pytest

import confirmations
from confirmations import (
    CONFIRM_PHRASE,
    ConfirmPhraseMismatchError,
    Proposal,
    ProposalAlreadyUsedError,
    ProposalExpiredError,
    ProposalNotFoundError,
    TokenMismatchError,
    _hash_token,
    create_proposal,
    validate_and_consume,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TABLE = "contacts"
SAMPLE_RECORD_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------

class TestCreateProposal:
    def test_returns_three_values(self):
        proposal_id, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        assert proposal_id
        assert token
        assert phrase == CONFIRM_PHRASE

    def test_proposal_id_is_unique(self):
        id1, _, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        id2, _, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        assert id1 != id2

    def test_token_is_unique(self):
        _, tok1, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        _, tok2, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        assert tok1 != tok2

    def test_token_not_stored_in_plaintext(self):
        proposal_id, token, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        stored = confirmations._get(proposal_id)
        assert stored is not None
        # The stored hash should NOT be the plaintext token
        assert stored.token_hash != token
        # It should be the SHA-256 hash of the token
        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# validate_and_consume — happy path
# ---------------------------------------------------------------------------

class TestValidateAndConsumeSuccess:
    def test_returns_proposal_on_success(self):
        pid, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        proposal = validate_and_consume(pid, token, phrase)
        assert proposal.table == SAMPLE_TABLE
        assert proposal.record_id == SAMPLE_RECORD_ID

    def test_marks_used_after_success(self):
        pid, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        validate_and_consume(pid, token, phrase)
        stored = confirmations._get(pid)
        assert stored.used is True


# ---------------------------------------------------------------------------
# validate_and_consume — failure modes
# ---------------------------------------------------------------------------

class TestValidateAndConsumeFailures:
    def test_not_found(self):
        with pytest.raises(ProposalNotFoundError, match="No proposal found"):
            validate_and_consume("nonexistent-id", "token", CONFIRM_PHRASE)

    def test_expired(self, monkeypatch):
        pid, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        # Manually rewrite created_at in Redis to simulate expiry
        stored = confirmations._get(pid)
        stored.created_at = time.time() - 9999
        confirmations._put(stored)
        with pytest.raises(ProposalExpiredError, match="expired"):
            validate_and_consume(pid, token, phrase)

    def test_expired_proposal_is_cleaned_up(self):
        pid, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        stored = confirmations._get(pid)
        stored.created_at = time.time() - 9999
        confirmations._put(stored)
        with pytest.raises(ProposalExpiredError):
            validate_and_consume(pid, token, phrase)
        # Should be removed from the store
        assert confirmations._get(pid) is None

    def test_replay_blocked(self):
        pid, token, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        validate_and_consume(pid, token, phrase)
        with pytest.raises(ProposalAlreadyUsedError, match="already been used"):
            validate_and_consume(pid, token, phrase)

    def test_wrong_phrase(self):
        pid, token, _ = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        with pytest.raises(ConfirmPhraseMismatchError, match="phrase mismatch"):
            validate_and_consume(pid, token, "WRONG PHRASE")

    def test_wrong_token(self):
        pid, _, phrase = create_proposal(SAMPLE_TABLE, SAMPLE_RECORD_ID)
        with pytest.raises(TokenMismatchError, match="does not match"):
            validate_and_consume(pid, "wrong-token-value", phrase)


# ---------------------------------------------------------------------------
# _hash_token
# ---------------------------------------------------------------------------

class TestHashToken:
    def test_deterministic(self):
        assert _hash_token("abc") == _hash_token("abc")

    def test_different_inputs_different_hashes(self):
        assert _hash_token("abc") != _hash_token("def")

    def test_is_sha256_hex(self):
        h = _hash_token("test")
        assert len(h) == 64
        int(h, 16)  # Should not raise — valid hex
