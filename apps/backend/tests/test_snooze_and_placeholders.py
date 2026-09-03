"""Snooze on a not-yet-due reminder, and generic medication placeholders."""

import inspect
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.reminder_guards import (  # noqa: E402
    ReminderNotDueError, ensure_snoozable,
)
from services.remivox.intents.extractors import (  # noqa: E402
    is_generic_medication_placeholder,
)
from services.remivox.intents import llm_extractor  # noqa: E402
import services.db_reminders as db_reminders  # noqa: E402


NOW = datetime(2026, 9, 2, 21, 23, tzinfo=timezone.utc)


def at(delta):
    return {"scheduled_time": (NOW + delta).isoformat()}


class SnoozeRefusesFutureReminders(unittest.TestCase):
    """A snooze must never move a reminder earlier than its scheduled time."""

    def test_tomorrow_morning_is_refused(self):
        # The reported case: due tomorrow 8 AM, snoozed at 9:23 PM, moved to
        # 9:52 PM today — about twelve hours early, a double-dose risk.
        with self.assertRaises(ReminderNotDueError) as ctx:
            ensure_snoozable(at(timedelta(hours=10, minutes=37)), now=NOW)
        self.assertIsNotNone(ctx.exception.scheduled_time)

    def test_even_a_minute_early_is_refused(self):
        with self.assertRaises(ReminderNotDueError):
            ensure_snoozable(at(timedelta(minutes=1)), now=NOW)

    def test_due_now_is_allowed(self):
        ensure_snoozable(at(timedelta(0)), now=NOW)

    def test_overdue_is_allowed(self):
        ensure_snoozable(at(timedelta(minutes=-20)), now=NOW)

    def test_missing_schedule_does_not_block(self):
        ensure_snoozable({}, now=NOW)


class SnoozeArithmetic(unittest.TestCase):
    def test_it_no_longer_computes_from_now_alone(self):
        src = inspect.getsource(db_reminders.snooze_reminder)
        self.assertIn("ensure_snoozable(reminder)", src)
        # max(now, scheduled) is the belt-and-braces half: even if the guard
        # were bypassed the new time cannot land before the dose was due.
        self.assertIn("base = max(now,", src)
        self.assertIn("base + timedelta(minutes=snooze_minutes)", src)
        self.assertNotIn("new_scheduled_time = now + timedelta", src)


class BothPathsShareTheRule(unittest.TestCase):
    """The voice and button routes must not drift on something this consequential."""

    def test_rest_route_handles_the_refusal(self):
        with open("route/reminders.py", encoding="utf-8") as fh:
            src = fh.read()
        start = src.index("async def snooze_reminder_post")
        body = src[start:start + 2000]
        self.assertIn("ReminderNotDueError", body)
        self.assertIn("HTTP_409_CONFLICT", body)

    def test_vox_route_handles_the_refusal(self):
        from services.remivox.actions import executor
        src = inspect.getsource(executor)
        start = src.index('if mode == "snooze":')
        body = src[start:start + 1600]
        self.assertIn("ReminderNotDueError", body)
        self.assertIn("not_due_yet", body)

    def test_vox_no_longer_reports_success_regardless(self):
        from services.remivox.actions import executor
        src = inspect.getsource(executor)
        start = src.index('if mode == "snooze":')
        body = src[start:start + 1600]
        # It used to discard the return value, so an already-snoozed reminder
        # still answered "snoozed" while nothing moved.
        self.assertIn("snoozed = await snooze_reminder", body)
        self.assertIn("if not snoozed:", body)
        self.assertIn("snooze_unavailable", body)


class GenericPlaceholders(unittest.TestCase):
    def test_placeholders_are_not_names(self):
        for t in ["medicine", "my medicine", "Pills", "my pills", "Medication",
                  "my meds", "tablets", "my tablets", "Vitamin", "prescription"]:
            self.assertTrue(is_generic_medication_placeholder(t), t)

    def test_real_medications_survive(self):
        for t in ["Metoprolol", "Vitamin D3", "CoQ10", "Atorvastatin", "Lyrica"]:
            self.assertFalse(is_generic_medication_placeholder(t), t)

    def test_a_title_that_merely_contains_the_word_survives(self):
        # Matching on substrings would lose information the patient did give.
        for t in ["medicine cabinet", "pick up my pills from the pharmacy",
                  "order more tablets", "collect prescription from Boots"]:
            self.assertFalse(is_generic_medication_placeholder(t), t)

    def test_empty_is_not_a_placeholder(self):
        self.assertFalse(is_generic_medication_placeholder(""))
        self.assertFalse(is_generic_medication_placeholder(None))


class PlaceholderCheckIsReached(unittest.TestCase):
    """Written-and-never-called is the failure this codebase keeps producing."""

    def test_clean_title_drops_placeholders(self):
        src = inspect.getsource(llm_extractor._clean_title)
        self.assertIn("is_generic_medication_placeholder(title)", src)
        self.assertIn("return None", src)

    def test_clean_title_still_keeps_the_vitamin_fix(self):
        src = inspect.getsource(llm_extractor._clean_title)
        self.assertIn("normalize_spoken_title(title)", src)
        # Order matters: "d3" must become "Vitamin D3" before the placeholder
        # check, or the bare word "vitamin" would never be reached correctly.
        self.assertLess(src.index("normalize_spoken_title(title)"),
                        src.index("is_generic_medication_placeholder(title)"))


if __name__ == "__main__":
    unittest.main()
