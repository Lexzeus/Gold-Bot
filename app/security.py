"""
Webhook authentication.

Defense in depth:
  1. HMAC-SHA256 over the RAW request body, compared in constant time.
     TradingView can't natively sign, so the recommended pattern is to put the
     precomputed signature in the `X-Signature` header via a relaying proxy, OR
     include `sig` inside the JSON payload computed over the stable fields.
     Both paths are supported below.
  2. Replay protection: payload `timestamp` must be recent.
  3. Optional static shared token check.
"""
from __future__ import annotations

import hashlib
import hmac
import time


def compute_signature(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: bytes, provided_sig: str | None) -> bool:
    # Fail closed on a missing/placeholder secret: otherwise an attacker who
    # guesses the server is unconfigured could forge HMACs with an empty key.
    if not secret or "replace_me" in secret or not provided_sig:
        return False
    expected = compute_signature(secret, raw_body)
    # constant-time compare to avoid timing attacks
    return hmac.compare_digest(expected, provided_sig.strip().lower())


def verify_token(expected_token: str | None, provided_token: str | None) -> bool:
    # If no token configured, this check is a no-op pass (HMAC is primary).
    if not expected_token or "replace_me" in expected_token:
        return True
    if not provided_token:
        return False
    return hmac.compare_digest(expected_token, provided_token)


def is_fresh(payload_ts: float | None, max_age_seconds: int) -> bool:
    """Reject stale/replayed alerts. If no timestamp present, fail closed."""
    if payload_ts is None:
        return False
    now = time.time()
    # tolerate small clock skew in the future (5s)
    return -5 <= (now - float(payload_ts)) <= max_age_seconds
