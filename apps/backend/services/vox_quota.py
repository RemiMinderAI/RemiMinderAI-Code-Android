"""
Vox usage quotas — the rules, kept free of DB and framework imports.

Two things live here because both need to be exercised directly by tests and
neither should be duplicated across the voice path and the REST path:

  * how many uses a plan gets, and when the window rolls
  * what actually counts as a "use"

The counting rule is the subtle half. One use is one complete user INTENT, not
one HTTP turn. A three-turn exchange that ends in a reminder being created is
one use, because the user asked for one thing and got one thing. Charging per
turn would penalise exactly the users who speak naturally rather than in a
memorised command format, which is the reason the LLM layer exists at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Agreed limits.
TRIAL_VOX_LIMIT = 15  # total, across the whole 14-day trial
PREMIUM_VOX_LIMIT = 300  # per rolling window
PREMIUM_PERIOD = timedelta(days=30)

# Blocked reasons the client must be able to tell apart. A trial user seeing a
# paywall is a conversion moment; a paying customer seeing one is an insult.
REASON_TRIAL_EXHAUSTED = "trial_quota_exhausted"
REASON_PREMIUM_EXHAUSTED = "premium_quota_exhausted"
REASON_TRIAL_EXPIRED = "trial_expired"
REASON_VOX_DISABLED = "vox_disabled"

# Outcomes that are the user's own doing: Vox understood, acted correctly, and
# the answer happened to be "no". These are real uses.
USER_CAUSED_REFUSALS = frozenset(
    {
        "no_match",
        "time_mismatch",
        "ambiguous_reminder",
        "medical_advice_refusal",
        "not_due_yet",
        "confirm_nothing_pending",
        # Understood exactly, and correctly refused: "as needed" is not a
        # schedule. Vox did its job, so the turn counts.
        "schedule_not_supported",
    }
)

# Outcomes that are OUR failure. The user asked for something and did not get
# it, so charging would bill them for our bug, our timeout, or our mishearing.
INTERNAL_FAILURES = frozenset(
    {
        "create_failed",
        "update_failed",
        "delete_failed",
        "checkin_failed",
        "unknown_intent",
        "restart_flow",
        "empty",
    }
)

# Turns that are mid-conversation or carry no request at all.
NON_TERMINAL_ACTIONS = frozenset(
    {
        "clarify",
        "briefing",  # user pressed Vox and said nothing
        "cancel_action",  # user changed their mind; they received nothing
        "restart_flow",
        "delete_reminder_confirm",  # still waiting on a yes/no
    }
)


def is_billable_turn(result: dict[str, Any]) -> bool:
    """
    Whether this completed turn should consume one Vox use.

    Charged only when the turn reached a terminal outcome for the user. A turn
    that is still gathering information, or that failed on our side, is free.
    """
    if not isinstance(result, dict):
        return False

    payload = result.get("action_payload")
    if isinstance(payload, dict) and isinstance(payload.get("pending"), dict):
        pending = payload["pending"]
        if pending.get("intent"):
            # Mid-conversation: the intent is not finished, so it is not a use
            # yet. The turn that finally completes it pays for the whole thing.
            return False

    action = str(result.get("action") or "")
    if action in NON_TERMINAL_ACTIONS:
        return False

    message_key = str(result.get("message_key") or "")
    if message_key in INTERNAL_FAILURES:
        return False
    if message_key in USER_CAUSED_REFUSALS:
        return True

    return bool(result.get("success"))


def current_period_start(
    anchor: Optional[datetime],
    now: Optional[datetime] = None,
) -> datetime:
    """
    The start of the window that [now] falls in, rolled forward in whole periods.

    Advancing by exact multiples (rather than resetting to now) keeps a user's
    reset day stable: someone who does not open the app for two months comes
    back to the same day of the month they started on, not a shifted one.
    """
    reference = now or datetime.now(timezone.utc)
    if anchor is None:
        return reference
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if reference < anchor + PREMIUM_PERIOD:
        return anchor
    elapsed = reference - anchor
    whole_periods = elapsed // PREMIUM_PERIOD
    return anchor + (PREMIUM_PERIOD * whole_periods)


def period_resets_at(period_start: datetime) -> datetime:
    return period_start + PREMIUM_PERIOD


def trial_remaining(used: int) -> int:
    return max(0, TRIAL_VOX_LIMIT - int(used or 0))


def premium_remaining(used: int) -> int:
    return max(0, PREMIUM_VOX_LIMIT - int(used or 0))
