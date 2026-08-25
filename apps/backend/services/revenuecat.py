"""
RevenueCat verification and webhook helpers.

The client telling us "I am premium" is a claim, not proof: any authenticated
user can POST it. Everything here exists so that claim is checked against
RevenueCat before it becomes `users.plan = PREMIUM`.

Never log receipts, tokens or subscriber payloads — event types, plan
transitions and booleans only.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

ENTITLEMENT_ID = "Premium"  # matches revenuecat_service.dart
API_BASE = "https://api.revenuecat.com/v1"
VERIFY_TIMEOUT_SECONDS = 8

# Event types that grant access, and those that revoke it. PRODUCT_CHANGE keeps
# access (the user swapped plan, they are still paying).
GRANTING_EVENTS = frozenset(
    {"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"}
)
REVOKING_EVENTS = frozenset({"EXPIRATION", "REFUND", "SUBSCRIPTION_PAUSED"})
# CANCELLATION means "will not renew" — access continues until EXPIRATION.
# BILLING_ISSUE likewise: RevenueCat retries, and access persists in grace.
NON_TRANSITION_EVENTS = frozenset({"CANCELLATION", "BILLING_ISSUE", "TRANSFER"})


class RevenueCatNotConfigured(Exception):
    """No secret key available, so a claim cannot be verified."""


def secret_key() -> Optional[str]:
    return (os.getenv("REVENUECAT_SECRET_KEY") or "").strip() or None


def allow_unverified() -> bool:
    """
    Explicit, deliberate opt-out from server-side verification.

    Defaults false so an unverified upgrade path can never ship by accident —
    it has to be switched on by someone who has read what it means.
    """
    raw = (os.getenv("REVENUECAT_ALLOW_UNVERIFIED") or "false").strip().lower()
    return raw in {"1", "true", "yes"}


def webhook_token() -> Optional[str]:
    return (os.getenv("REVENUECAT_WEBHOOK_TOKEN") or "").strip() or None


def webhook_authorized(header_value: Optional[str]) -> bool:
    """
    Constant-time-ish check of the webhook Authorization header.

    Fails closed: with no configured token, nothing is accepted. An open webhook
    endpoint is a free premium upgrade for anyone who finds the URL.
    """
    expected = webhook_token()
    if not expected:
        logger.error(
            "REVENUECAT_WEBHOOK_TOKEN is not set — rejecting webhook delivery"
        )
        return False
    if not header_value:
        return False
    supplied = header_value.strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    # hmac.compare_digest semantics without importing hmac for one call.
    if len(supplied) != len(expected):
        return False
    mismatch = 0
    for a, b in zip(supplied, expected):
        mismatch |= ord(a) ^ ord(b)
    return mismatch == 0


def entitlement_active(subscriber: dict[str, Any]) -> bool:
    """True when the Premium entitlement exists and has not expired."""
    entitlements = (subscriber or {}).get("entitlements") or {}
    entry = entitlements.get(ENTITLEMENT_ID)
    if not isinstance(entry, dict):
        return False
    expires = entry.get("expires_date")
    if not expires:
        # Lifetime / non-expiring entitlement.
        return True
    try:
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except ValueError:
        return False
    return when > datetime.now(timezone.utc)


def verify_premium(app_user_id: str) -> bool:
    """
    Ask RevenueCat directly whether this subscriber holds Premium.

    Raises RevenueCatNotConfigured when no secret key is available, so callers
    must decide explicitly rather than silently trusting the client.
    """
    key = secret_key()
    if not key:
        raise RevenueCatNotConfigured("REVENUECAT_SECRET_KEY not set")
    if not app_user_id:
        return False

    response = requests.get(
        f"{API_BASE}/subscribers/{app_user_id}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=VERIFY_TIMEOUT_SECONDS,
    )
    if response.status_code == 404:
        logger.info("RevenueCat has no subscriber record; treating as not premium")
        return False
    response.raise_for_status()
    subscriber = (response.json() or {}).get("subscriber") or {}
    return entitlement_active(subscriber)


def premium_after_event(event_type: str, event: dict[str, Any]) -> Optional[bool]:
    """
    Whether an event should grant (True), revoke (False) or leave access alone.

    None means "no transition": a cancellation is a statement about renewal, not
    a loss of access today, and revoking early would cut off time already paid for.
    """
    kind = (event_type or "").strip().upper()
    if kind in GRANTING_EVENTS:
        return True
    if kind in REVOKING_EVENTS:
        return False
    if kind in NON_TRANSITION_EVENTS:
        return None
    logger.info("Unhandled RevenueCat event type=%s — no plan change", kind or "?")
    return None
