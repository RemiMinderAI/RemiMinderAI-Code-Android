"""
Safety rules for reminder state changes.

Deliberately free of database and framework imports so the rule can be exercised
directly by tests and reused by every caller — the Vox voice path and the REST
endpoint must not be able to drift apart on something this consequential.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# A patient may legitimately take a dose a little early — before leaving the
# house, or with an earlier meal — so completion is allowed slightly ahead of the
# scheduled time rather than only at or after it.
#
# 2 hours sits far below the gap to the next dose on every common schedule (24h
# daily, 12h twice-daily). That margin is the point: once the window can reach
# the following instance, "taken early" becomes indistinguishable from
# "completed tomorrow's dose", which is how a double-tap silently skipped a day
# of medication and recorded it as taken.
EARLY_COMPLETION_GRACE = timedelta(hours=2)


class ReminderNotDueError(Exception):
    """
    Completion refused because the reminder is too far in the future.

    Carries the reminder so callers can say WHEN it is actually due instead of
    returning a generic failure.
    """

    def __init__(self, reminder: Dict[str, Any], scheduled_time: Optional[datetime]):
        self.reminder = reminder
        self.scheduled_time = scheduled_time
        super().__init__("Reminder is not due yet")


def as_utc(value: Any) -> Optional[datetime]:
    """Normalize a DB/ISO scheduled time to timezone-aware UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def early_completion_grace(reminder: Dict[str, Any]) -> timedelta:
    """
    How far ahead of schedule this particular reminder may be completed.

    Grace shrinks for tightly spaced schedules: an every-4-hours reminder must
    not accept completion 2 hours early, or the window covers half the gap to the
    next dose. Half the interval keeps "early" unambiguous at any spacing.
    """
    if str(reminder.get("recurrence") or "").strip().lower() == "interval":
        try:
            hours = int(reminder.get("interval_hours") or 0)
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            return min(EARLY_COMPLETION_GRACE, timedelta(hours=hours) / 2)
    return EARLY_COMPLETION_GRACE


def ensure_completable(reminder: Dict[str, Any], now: Optional[datetime] = None) -> None:
    """
    Raise ReminderNotDueError if this dose is not due yet.

    A completed row is a claim that a patient took a dose, so the failure
    direction matters: refusing a legitimate early tap is recoverable, recording
    a dose that was never taken is not.
    """
    scheduled = as_utc(reminder.get("scheduled_time"))
    if scheduled is None:
        return
    reference = now or datetime.now(timezone.utc)
    if scheduled - reference > early_completion_grace(reminder):
        raise ReminderNotDueError(reminder, scheduled)


def ensure_snoozable(reminder: Dict[str, Any], now: Optional[datetime] = None) -> None:
    """
    Raise ReminderNotDueError if this reminder has not come due yet.

    Snooze moves a reminder forward from *now*, which is right for something
    already overdue and wrong for anything else: snoozing a dose due tomorrow at
    8 AM moved it to half an hour from now, roughly twelve hours early. A
    medication reminder that arrives before the dose is due is a double-dose
    risk, so there is no version of "snooze" that may move a reminder earlier
    than its scheduled time.

    Unlike completion there is no grace window. Taking a dose slightly early is
    a real thing a patient does; snoozing a dose that has not been offered yet
    is not — the reminder has not fired, so there is nothing to postpone.
    """
    scheduled = as_utc(reminder.get("scheduled_time"))
    if scheduled is None:
        return
    reference = now or datetime.now(timezone.utc)
    if scheduled > reference:
        raise ReminderNotDueError(reminder, scheduled)
