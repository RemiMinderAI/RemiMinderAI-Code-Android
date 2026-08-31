#!/usr/bin/env python3
"""
End-to-end regression suite for the Vox care pipeline (Part 6).

Drives run_care_turn() directly with transcripts — no audio, no STT, no TTS —
and asserts on the resolved intent, the reminder actually written, and the
spoken reply.

The database and reminder service are replaced with an in-memory fake, so this
never touches Cloud SQL. Only the NLU layer talks to the outside world, and only
in --nlu=llm mode.

Usage:
    python tests/test_vox_e2e.py                # canned NLU responses, no network
    python tests/test_vox_e2e.py --nlu=llm      # live Gemini (needs GEMINI_API_KEY)
    python tests/test_vox_e2e.py --nlu=regex    # legacy regex fallback path
    python tests/test_vox_e2e.py --only 3 7 20  # run selected cases
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import types
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

# Import the backend package from tests/ (repo convention: suites live here).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TZ = "America/Toronto"
USER = "test-user-uuid"


# ---------------------------------------------------------------------------
# In-memory stand-ins for the DB-backed service layer.
# Installed into sys.modules BEFORE the pipeline is imported.
# ---------------------------------------------------------------------------

class FakeStore:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.updated: list[tuple[str, Any]] = []
        self.completed: list[str] = []
        self.snoozed: list[tuple[str, int]] = []
        self.skipped: list[str] = []
        self.deleted: list[str] = []
        self.recurring_created: list[str] = []

    def reset(self) -> None:
        self.__init__()


STORE = FakeStore()


# The reminders passed into the current turn, so the fake service can apply the
# real guard to the same rows the executor matched against.
CURRENT_REMINDERS: dict = {}


def _find_fixture_reminder(reminder_id: str):
    for bucket in (CURRENT_REMINDERS or reminders_fixture()).values():
        for item in bucket:
            if str(item.get("id")) == str(reminder_id):
                return item
    return None


def _install_stubs() -> None:
    svc = types.ModuleType("services.reminder_service")

    async def create_new_reminder(data):
        STORE.created.append(data)
        # Mirrors production: the next recurring instance is created once, by the
        # DB layer, when an instance is completed — never at creation time.
        return {"id": f"new-{len(STORE.created)}", "title": data.title}

    async def update_reminder_details(reminder_id, user_id, updates):
        STORE.updated.append((reminder_id, updates))
        return {"id": reminder_id, "title": "updated"}

    async def cancel_reminder(reminder_id, user_id):
        STORE.deleted.append(reminder_id)
        return True

    async def complete_reminder(reminder_id, user_id, action):
        # Apply the production guard, not a copy of it, so this test fails if the
        # real rule regresses.
        from services.reminder_guards import ensure_completable

        item = _find_fixture_reminder(reminder_id)
        if item is not None:
            ensure_completable(item)
        STORE.completed.append(reminder_id)
        # Exactly one next instance per completion (the dupe bug regressed twice).
        STORE.recurring_created.append(reminder_id)
        return {"id": reminder_id}

    async def snooze_reminder(reminder_id, user_id, minutes=30):
        STORE.snoozed.append((reminder_id, minutes))
        return {"id": reminder_id}

    async def skip_reminder(reminder_id, user_id, action):
        STORE.skipped.append(reminder_id)
        return {"id": reminder_id}

    async def list_patient_reminders(user_id):
        return reminders_fixture()

    from services.reminder_guards import ReminderNotDueError as _NotDue

    svc.ReminderNotDueError = _NotDue
    svc.create_new_reminder = create_new_reminder
    svc.update_reminder_details = update_reminder_details
    svc.cancel_reminder = cancel_reminder
    svc.complete_reminder = complete_reminder
    svc.snooze_reminder = snooze_reminder
    svc.skip_reminder = skip_reminder
    svc.list_patient_reminders = list_patient_reminders
    sys.modules["services.reminder_service"] = svc

    intents = types.ModuleType("services.remivox_intents")

    def build_briefing(*_args, **_kwargs):
        return "Hi — I'm Vox. Here is your day."

    async def build_caregiver_brief(*_args, **_kwargs):
        return "Caregiver brief."

    intents.build_briefing = build_briefing
    intents.build_caregiver_brief = build_caregiver_brief
    sys.modules["services.remivox_intents"] = intents


_install_stubs()

from services.remivox.intents import llm_extractor  # noqa: E402
from services.remivox.pipeline import run_care_turn  # noqa: E402
from services.remivox.state.conversation import clear_state  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def reminders_fixture() -> dict[str, list[dict]]:
    return {
        "today": [
            {
                "id": "r-metoprolol",
                "title": "Metoprolol",
                "reminder_type": "medication",
                "status": "pending",
                "recurrence": "daily",
            },
            {
                "id": "r-vitamind3",
                "title": "Vitamin D3",
                "reminder_type": "medication",
                "status": "pending",
                "recurrence": "daily",
            },
            {
                "id": "r-pregabalin",
                "title": "Pregabalin",
                "reminder_type": "medication",
                "status": "pending",
                "recurrence": "daily",
            },
        ],
        "upcoming": [
            {
                "id": "r-labpickup",
                "title": "Lab pickup",
                "reminder_type": "task",
                "status": "pending",
                "recurrence": "once",
            },
        ],
        "past": [],
    }


def _at_local(hhmm: str, *, days: int = 0):
    """A UTC scheduled_time for a given local wall-clock time, N days out."""
    from zoneinfo import ZoneInfo

    hour, minute = (int(p) for p in hhmm.split(":"))
    local = datetime.now(ZoneInfo(TZ)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ) + timedelta(days=days)
    return local.astimezone(timezone.utc)


def collision_fixture() -> dict:
    """Two same-titled Metoprolol reminders, 08:00 and 22:00 local."""
    return {
        "today": [
            {"id": "r-met-0800", "title": "Metoprolol", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _at_local("08:00")},
            {"id": "r-met-2200", "title": "Metoprolol", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _at_local("22:00")},
        ],
        "upcoming": [],
        "past": [],
    }


def due_fixture() -> dict:
    """One reminder due earlier today, one not due until tomorrow."""
    return {
        "today": [
            {"id": "r-due-today", "title": "Metoprolol", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _at_local("00:05")},
        ],
        "upcoming": [
            {"id": "r-due-tomorrow", "title": "Amlodipine", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _at_local("22:00", days=1)},
        ],
        "past": [],
    }


SUMMARIES = [
    {
        "title": "Visit with Dr. Gubbala",
        "summary_preview": "Blood pressure improved; continue Metoprolol.",
    }
]


def today() -> date:
    """
    "Today" as the USER experiences it, in TZ — not in UTC.

    The pipeline is told timezone_name=TZ and resolves "tomorrow" against that.
    Computing the expectation in UTC made this suite fail for the four hours
    between UTC midnight and local midnight, blaming the product for the
    harness being in the wrong timezone.
    """
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(TZ)).date()


def next_weekday(name: str, at_hhmm: Optional[str] = None) -> date:
    """
    The date a user means by a bare weekday name.

    Mirrors executor._scheduled_from_parts: if today IS that weekday, "Thursday
    at 3 PM" means today when 3 PM is still ahead, and next week once it has
    passed. Callers that pass no time keep the conservative next-week reading.
    """
    names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo(TZ))
    wanted = names.index(name)
    base = now.date()
    delta = (wanted - base.weekday()) % 7
    if delta == 0:
        still_ahead = False
        if at_hhmm:
            hour, minute = (int(part) for part in at_hhmm.split(":", 1))
            still_ahead = (now.hour, now.minute) < (hour, minute)
        if not still_ahead:
            delta = 7
    return base + timedelta(days=delta)


# ---------------------------------------------------------------------------
# Canned NLU responses (--nlu=mock) so the suite runs with no network.
# Keys are the exact transcripts below.
# ---------------------------------------------------------------------------

def _mock_table() -> dict[str, dict]:
    t = today().isoformat()
    tomorrow = (today() + timedelta(days=1)).isoformat()
    thursday = next_weekday("thursday", "15:00").isoformat()
    return {
        # --- iOS device-test defects. Values mirror what the live model
        # --- actually returned, so mock exercises the same branches.
        "the 8 AM one": {
            "intent": "COMPLETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00"},
            "confidence": 0.95,
        },
        "Set a reminder to take my pills today at 2:31 PM": {
            "intent": "CREATE_REMINDER",
            # No frequency: the stated date is what makes it a one-off.
            "entities": {"title": "take my pills", "reminder_type": "medication",
                         "time": "14:31", "date": t},
            "confidence": 0.95,
        },
        "create a task for Doctor appointment tomorrow at 10 AM": {
            "intent": "CREATE_REMINDER",
            # The model reads the SUBJECT as an appointment; the spoken word
            # "task" is what must win, so this is left wrong on purpose.
            "entities": {"title": "Doctor appointment",
                         "reminder_type": "appointment",
                         "time": "10:00", "date": tomorrow},
            "confidence": 0.95,
        },
        "Doctor appointment with Doctor Gubbala on Thursday at 3 PM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Doctor appointment with Doctor Gubbala",
                         "reminder_type": "appointment", "time": "15:00",
                         "date": thursday, "day_of_week": "thursday",
                         "doctor_name": "Dr. Gubbala"},
            "confidence": 0.98,
        },
        "Set a task to pick up lab results tomorrow at 10 AM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "pick up lab results", "reminder_type": "task",
                         "time": "10:00", "date": tomorrow, "frequency": "once"},
            "confidence": 1.0,
        },
        "tax to pick up lab results tomorrow at 10 AM": {
            "intent": "CREATE_REMINDER",
            # "tax to" is how the recogniser renders "task to"; the leading
            # phrase is left in so the title cleanup is exercised here too.
            "entities": {"title": "tax to pick up lab results",
                         "reminder_type": "task",
                         "time": "10:00", "date": tomorrow},
            "confidence": 0.92,
        },
        "Set a reminder for Metoprolol at 10 AM every day": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "10:00", "frequency": "daily"},
            "confidence": 0.98,
        },
        "Remind me to take Pregabalin at 8:30 PM daily": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Pregabalin", "reminder_type": "medication",
                         "time": "20:30", "frequency": "daily"},
            "confidence": 0.98,
        },
        "Metoprolol reminder every 4 hours starting at 8 AM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00", "frequency": "interval",
                         "interval_hours": 4},
            "confidence": 0.95,
        },
        "Add Vitamin D3 every Monday Wednesday and Friday at 9 AM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Vitamin D3", "reminder_type": "medication",
                         "time": "09:00", "frequency": "custom",
                         "custom_days": ["monday", "wednesday", "friday"]},
            "confidence": 0.95,
        },
        "Set a lab pickup task tomorrow at 10 AM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Lab pickup", "reminder_type": "task",
                         "time": "10:00", "date": tomorrow},
            "confidence": 0.95,
        },
        "Doctor appointment with Dr. Gubbala on Thursday at 3 PM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Doctor appointment with Dr. Gubbala",
                         "reminder_type": "appointment", "time": "15:00",
                         "date": thursday, "day_of_week": "thursday",
                         "doctor_name": "Dr. Gubbala"},
            "confidence": 0.95,
        },
        "Set a reminder for Metoprolol": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication"},
            "confidence": 0.9,
        },
        "Set up a reminder for my lab pickup at 10 AM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Lab pickup", "reminder_type": "task",
                         "time": "10:00"},
            "confidence": 0.9,
        },
        "Remind me about my medicine at 8 PM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": None, "reminder_type": "medication",
                         "time": "20:00"},
            "confidence": 0.85,
        },
        "Set a medication reminder for Gonarex": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Gonarex", "reminder_type": "medication"},
            "confidence": 0.9,
        },
        "8 PM": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Gonarex", "reminder_type": "medication",
                         "time": "20:00"},
            "confidence": 0.9,
        },
        "every day": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Gonarex", "reminder_type": "medication",
                         "time": "20:00", "frequency": "daily"},
            "confidence": 0.9,
        },
        "Mark done Metoprolol": {
            "intent": "COMPLETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "I took my Metoprolol": {
            "intent": "COMPLETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "Snooze Vitamin D3": {
            "intent": "SNOOZE_REMINDER",
            "entities": {"title": "Vitamin D3", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "Change my Metoprolol to 10 PM": {
            "intent": "UPDATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "new_time": "22:00"},
            "confidence": 0.95,
        },
        "Cancel my Pregabalin reminder": {
            "intent": "DELETE_REMINDER",
            "entities": {"title": "Pregabalin", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "yes": {"intent": "CONFIRM", "entities": {}, "confidence": 1.0},
        "What are my medications today?": {
            "intent": "READ_TODAY_MEDICATIONS", "entities": {}, "confidence": 0.98,
        },
        "Read me my last visit summary": {
            "intent": "READ_DOCTOR_SUMMARY", "entities": {}, "confidence": 0.95,
        },
        "What did my doctor say last time?": {
            "intent": "READ_DOCTOR_SUMMARY", "entities": {}, "confidence": 0.95,
        },
        "Yes": {"intent": "CONFIRM", "entities": {}, "confidence": 1.0},
        "mmm uhh": {"intent": "UNKNOWN", "entities": {}, "confidence": 0.2},
        "Delete everything": {
            "intent": "DELETE_REMINDER",
            "entities": {"title": None, "reminder_type": "medication"},
            "confidence": 0.6,
        },
        "uhh hmm err": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Gonarex", "reminder_type": "medication"},
            "confidence": 0.6,
        },
        "I took my Amlodipine": {
            "intent": "COMPLETE_REMINDER",
            "entities": {"title": "Amlodipine", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "Cancel Metoprolol at 8 AM": {
            "intent": "DELETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00"},
            "confidence": 0.95,
        },
        "Cancel my Metoprolol": {
            "intent": "DELETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "Cancel Metoprolol at 3 AM": {
            "intent": "DELETE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "03:00"},
            "confidence": 0.95,
        },
        "Set a reminder for Metoprolol at 8 AM on weekdays": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00", "frequency": "custom",
                         "custom_days": ["monday","tuesday","wednesday","thursday","friday"]},
            "confidence": 0.95,
        },
        "Add Vitamin D at 9 AM on weekends": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Vitamin D", "reminder_type": "medication",
                         "time": "09:00", "frequency": "custom",
                         "custom_days": ["saturday","sunday"]},
            "confidence": 0.95,
        },
        "Set a reminder for Metoprolol at 8 AM every other day": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00", "frequency": "daily", "interval_count": 2},
            "confidence": 0.95,
        },
        "Set a reminder for Metoprolol at 8 AM biweekly": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00", "frequency": None,
                         "frequency_ambiguous": "biweekly"},
            "confidence": 0.6,
        },
        "Set a reminder for Metoprolol at 8 AM every few days": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Metoprolol", "reminder_type": "medication",
                         "time": "08:00", "frequency": None},
            "confidence": 0.6,
        },
        "I took my Ibuprofen": {
            "intent": "COMPLETE_REMINDER",
            "entities": {"title": "Ibuprofen", "reminder_type": "medication"},
            "confidence": 0.95,
        },
        "Pregavelin at eighty thirty PM everyday": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Pregabalin", "reminder_type": "medication",
                         "time": "20:30", "frequency": "daily"},
            "confidence": 0.85,
        },
    }


def _assert_no_duplicate_transcripts() -> None:
    """
    A repeated key in the mock table is invisible: the later one silently wins.

    That is not hypothetical — two entries for the same transcript made a fix
    look broken in mock while passing against the live model.
    """
    import re as _re

    source = Path(__file__).read_text(encoding="utf-8")
    body = source.split("def _mock_table(", 1)[1].split("\ndef ", 1)[0]
    keys = _re.findall(r'^        "(.+?)": \{$', body, _re.M)
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise Failure(f"duplicate mock transcripts: {sorted(dupes)}")


def install_mock_nlu() -> None:
    table = _mock_table()
    _assert_no_duplicate_transcripts()

    # "the 8 AM one" is an ANSWER to a disambiguation question, and the time in
    # it depends on the fixture, which is anchored to now. Canning every
    # possible clock time is impossible, so model the shape instead — this is
    # what the live model returns for that phrasing (verified against it).
    answer_re = re.compile(
        r"^the\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s+one$", re.IGNORECASE
    )

    async def fake_call(user_message: str) -> dict:
        # The transcript is the last line of the composed user message.
        transcript = user_message.rsplit("Transcript: ", 1)[-1].strip()
        if transcript in table:
            return json.loads(json.dumps(table[transcript]))

        match = answer_re.match(transcript)
        if match:
            hour = int(match.group(1)) % 12
            if match.group(3).lower() == "pm":
                hour += 12
            minute = int(match.group(2) or 0)
            return {
                "intent": "COMPLETE_REMINDER",
                "entities": {
                    "title": "Metoprolol",
                    "reminder_type": "medication",
                    "time": f"{hour:02d}:{minute:02d}",
                },
                "confidence": 0.95,
            }

        raise llm_extractor.ExtractionError(
            f"no canned NLU response for {transcript!r}"
        )

    llm_extractor._call_gemini = fake_call  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

class Failure(Exception):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


async def say(
    transcript: str,
    *,
    session: str,
    reminders: Optional[dict] = None,
) -> dict:
    global CURRENT_REMINDERS
    reminders = reminders or reminders_fixture()
    CURRENT_REMINDERS = reminders
    return await run_care_turn(
        user_uuid=USER,
        text=transcript,
        language="en",
        reminders=reminders,
        summaries=SUMMARIES,
        timezone_name=TZ,
        session_id=session,
    )


def only_created(index: int = 0):
    expect(bool(STORE.created), "expected a reminder to be created, none was")
    return STORE.created[index]


def local_hhmm(dt: datetime) -> str:
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo(TZ)).strftime("%H:%M")


def local_date(dt: datetime) -> str:
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo(TZ)).date().isoformat()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

async def case_01(s):
    out = await say("Set a reminder for Metoprolol at 10 AM every day", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.title == "Metoprolol", f"title={r.title!r}")
    expect(local_hhmm(r.scheduled_time) == "10:00", f"time={local_hhmm(r.scheduled_time)}")
    expect(r.recurrence == "daily", f"recurrence={r.recurrence}")


async def case_02(s):
    out = await say("Remind me to take Pregabalin at 8:30 PM daily", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.title == "Pregabalin", f"title={r.title!r}")
    expect(local_hhmm(r.scheduled_time) == "20:30", f"time={local_hhmm(r.scheduled_time)}")
    expect(r.recurrence == "daily", f"recurrence={r.recurrence}")


async def case_03(s):
    out = await say("Metoprolol reminder every 4 hours starting at 8 AM", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.recurrence == "interval", f"recurrence={r.recurrence}")
    expect(r.interval_hours == 4, f"interval_hours={r.interval_hours}")
    expect(local_hhmm(r.scheduled_time) == "08:00", f"time={local_hhmm(r.scheduled_time)}")
    expect("4 hours" in out["text"], f"reply={out['text']!r}")


async def case_04(s):
    out = await say("Add Vitamin D3 every Monday Wednesday and Friday at 9 AM", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.recurrence == "custom", f"recurrence={r.recurrence}")
    expect(r.custom_days == ["monday", "wednesday", "friday"], f"days={r.custom_days}")
    expect(local_hhmm(r.scheduled_time) == "09:00", f"time={local_hhmm(r.scheduled_time)}")


async def case_05(s):
    out = await say("Set a lab pickup task tomorrow at 10 AM", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.reminder_type == "task", f"type={r.reminder_type}")
    expect(r.recurrence == "once", f"recurrence={r.recurrence}")
    tomorrow = (today() + timedelta(days=1)).isoformat()
    expect(local_date(r.scheduled_time) == tomorrow,
           f"date={local_date(r.scheduled_time)} expected {tomorrow}")


async def case_06(s):
    out = await say("Doctor appointment with Dr. Gubbala on Thursday at 3 PM", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.reminder_type == "appointment", f"type={r.reminder_type}")
    expect("Gubbala" in r.title, f"title={r.title!r}")
    expect("3 PM" not in r.title and "Thursday" not in r.title,
           f"title still carries schedule words: {r.title!r}")
    expect(local_hhmm(r.scheduled_time) == "15:00", f"time={local_hhmm(r.scheduled_time)}")
    expected = next_weekday("thursday", "15:00").isoformat()
    expect(local_date(r.scheduled_time) == expected,
           f"date={local_date(r.scheduled_time)} expected {expected}")


async def case_07(s):
    out = await say("Set a reminder for Metoprolol", session=s)
    expect(not STORE.created, "must not create with no time")
    text = out["text"].lower()
    expect("time" in text, f"should ask the time: {out['text']!r}")
    expect("every day" not in text and "once" not in text,
           f"must ask ONE question (time), not frequency too: {out['text']!r}")


async def case_08(s):
    out = await say("Set up a reminder for my lab pickup at 10 AM", session=s)
    expect(not STORE.created, "must not create before the day is known")
    text = out["text"].lower()
    expect("day" in text, f"should ask which day: {out['text']!r}")


async def case_09(s):
    out = await say("Remind me about my medicine at 8 PM", session=s)
    expect(not STORE.created, "must not create without a medicine name")
    text = out["text"].lower()
    expect("medicine" in text or "call this" in text,
           f"should ask which medicine: {out['text']!r}")


async def case_10(s):
    out = await say("Set a medication reminder for Gonarex", session=s)
    expect(not STORE.created, "no create yet")
    expect("time" in out["text"].lower(), f"ask time: {out['text']!r}")

    out = await say("8 PM", session=s)
    expect(not STORE.created, "still no create — frequency unknown")
    text = out["text"].lower()
    expect("every day" in text or "once" in text, f"ask frequency: {out['text']!r}")
    expect("gonarex" in text, f"should reference the medication: {out['text']!r}")

    out = await say("every day", session=s)
    r = only_created()
    expect(r.title == "Gonarex", f"title={r.title!r}")
    expect(local_hhmm(r.scheduled_time) == "20:00", f"time={local_hhmm(r.scheduled_time)}")
    expect(r.recurrence == "daily", f"recurrence={r.recurrence}")


async def case_11(s):
    await say("Mark done Metoprolol", session=s)
    expect(STORE.completed == ["r-metoprolol"], f"completed={STORE.completed}")
    expect(len(STORE.recurring_created) == 1,
           f"expected exactly ONE recurring instance, got {len(STORE.recurring_created)}")

    STORE.reset()
    await say("I took my Metoprolol", session=s + "-b")
    expect(STORE.completed == ["r-metoprolol"], f"completed={STORE.completed}")
    expect(len(STORE.recurring_created) == 1,
           f"expected exactly ONE recurring instance, got {len(STORE.recurring_created)}")


async def case_12(s):
    await say("Snooze Vitamin D3", session=s)
    expect(STORE.snoozed == [("r-vitamind3", 30)], f"snoozed={STORE.snoozed}")


async def case_13(s):
    out = await say("Change my Metoprolol to 10 PM", session=s)
    expect(out["intent"] == "UPDATE_REMINDER", f"intent={out['intent']}")
    expect(len(STORE.updated) == 1, f"updates={STORE.updated}")
    rid, updates = STORE.updated[0]
    expect(rid == "r-metoprolol", f"target={rid}")
    fields = updates.model_dump(exclude_unset=True)
    expect(local_hhmm(fields["scheduled_time"]) == "22:00",
           f"new time={fields.get('scheduled_time')}")
    expect(fields.get("title") in (None,), f"title must not change: {fields}")


async def case_14(s):
    out = await say("Cancel my Pregabalin reminder", session=s)
    expect(STORE.deleted == ["r-pregabalin"],
           f"expected direct delete, got deleted={STORE.deleted} text={out['text']!r}")


async def case_15(s):
    out = await say("What are my medications today?", session=s)
    expect(out["intent"] == "READ_TODAY_MEDICATIONS", f"intent={out['intent']}")
    names = out["action_payload"].get("names") or []
    expect("Metoprolol" in names, f"names={names}")
    expect("Lab pickup" not in names, f"tasks must be excluded: {names}")


async def case_16(s):
    for phrase in ("Read me my last visit summary", "What did my doctor say last time?"):
        STORE.reset()
        out = await say(phrase, session=s + phrase[:4])
        expect(out["intent"] == "READ_DOCTOR_SUMMARY", f"{phrase!r} -> {out['intent']}")
        expect("Blood pressure improved" in out["text"], f"reply={out['text']!r}")


async def case_17(s):
    out = await say("Yes", session=s)
    expect(not STORE.deleted and not STORE.created, "nothing may happen")
    expect("waiting for confirmation" in out["text"].lower(),
           f"reply={out['text']!r}")


async def case_18(s):
    out = await say("mmm uhh", session=s)
    expect(not STORE.created, "garbled speech must not create anything")
    text = out["text"].lower()
    expect("didn't catch" in text or "not sure" in text or "repeat" in text,
           f"reply={out['text']!r}")
    expect("i'm vox" not in text, f"must not fall back to the briefing: {out['text']!r}")


async def case_19(s):
    out = await say("Delete everything", session=s)
    expect(not STORE.deleted, f"must NOT bulk delete, deleted={STORE.deleted}")
    expect("which" in out["text"].lower(), f"should ask which one: {out['text']!r}")


async def case_20(s):
    out = await say("Pregavelin at eighty thirty PM everyday", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.title == "Pregabalin", f"STT artifact not corrected: {r.title!r}")
    expect(local_hhmm(r.scheduled_time) == "20:30", f"time={local_hhmm(r.scheduled_time)}")
    expect(r.recurrence == "daily", f"recurrence={r.recurrence}")


async def case_21(s):
    """Two unanswered clarification rounds, then Vox restarts the request."""
    out = await say("Set a medication reminder for Gonarex", session=s)
    expect("time" in out["text"].lower(), f"round 1: {out['text']!r}")

    # Unintelligible, and not confusable with "never mind" — this case is about
    # the round limit, not about abandoning the request.
    out = await say("uhh hmm err", session=s)
    expect("time" in out["text"].lower(), f"round 2: {out['text']!r}")

    out = await say("uhh hmm err", session=s)
    expect("start over" in out["text"].lower(),
           f"round 3 should restart: {out['text']!r}")
    expect(not STORE.created, "nothing may be created")

    # State was cleared: the next utterance starts a clean request.
    out = await say("Set a reminder for Metoprolol at 10 AM every day", session=s)
    expect(bool(STORE.created), "a complete command after restart must execute")


async def case_22(s):
    """Mark done a reminder due today -> succeeds, exactly one next instance."""
    out = await say("I took my Metoprolol", session=s, reminders=due_fixture())
    expect(STORE.completed == ["r-due-today"], f"completed={STORE.completed}")
    expect(len(STORE.recurring_created) == 1,
           f"expected exactly ONE next instance, got {len(STORE.recurring_created)}")
    expect("logged" in out["text"].lower(), f"reply={out['text']!r}")


async def case_23(s):
    """Mark done a reminder due tomorrow -> refused, nothing written."""
    out = await say("I took my Amlodipine", session=s, reminders=due_fixture())
    expect(not STORE.completed, f"must not complete a future dose: {STORE.completed}")
    expect(not STORE.recurring_created,
           f"must not spawn an instance: {STORE.recurring_created}")
    text = out["text"].lower()
    expect("isn't due" in text or "not due" in text, f"reply={out['text']!r}")
    expect("tomorrow" in text, f"should say when it IS due: {out['text']!r}")


async def case_24(s):
    """Colliding titles + a stated time -> cancels the right one."""
    await say("Cancel Metoprolol at 8 AM", session=s, reminders=collision_fixture())
    expect(STORE.deleted == ["r-met-0800"],
           f"expected the 08:00 one, got {STORE.deleted}")


async def case_25(s):
    """Colliding titles, no time -> still asks which (must not guess)."""
    out = await say("Cancel my Metoprolol", session=s, reminders=collision_fixture())
    expect(not STORE.deleted, f"must not guess: {STORE.deleted}")
    expect("which" in out["text"].lower(), f"reply={out['text']!r}")


async def case_26(s):
    """Stated time matches nothing -> says so, never falls back to another."""
    out = await say("Cancel Metoprolol at 3 AM", session=s, reminders=collision_fixture())
    expect(not STORE.deleted, f"must not delete a different one: {STORE.deleted}")
    text = out["text"].lower()
    expect("don't see" in text or "do not see" in text, f"reply={out['text']!r}")


# ---------------------------------------------------------------------------
# Usage quota (services/vox_quota.py). These exercise the real rules directly —
# the counting decision is pure, so it is tested without a DB.
# ---------------------------------------------------------------------------

def _turn(action, success, message_key, pending=None):
    payload = {"pending": pending} if pending else {}
    return {"action": action, "success": success,
            "message_key": message_key, "action_payload": payload}


async def case_27(s):
    """A multi-turn intent costs exactly one use, not one per turn."""
    from services.vox_quota import is_billable_turn

    # "Set a reminder for Metoprolol" -> asks time
    t1 = _turn("clarify", True, "clarify_missing_slots",
               pending={"intent": "CREATE_REMINDER"})
    # "8 PM" -> asks frequency
    t2 = _turn("clarify", True, "clarify_missing_slots",
               pending={"intent": "CREATE_REMINDER"})
    # "every day" -> created
    t3 = _turn("create_reminder", True, "created")

    charged = [is_billable_turn(t) for t in (t1, t2, t3)]
    expect(charged == [False, False, True],
           f"3-turn intent should charge once at the end, got {charged}")


async def case_28(s):
    """Our own failures are free; user-caused refusals are not."""
    from services.vox_quota import is_billable_turn

    free = {
        "LLM fell back to UNKNOWN": _turn("unknown_intent", False, "unknown_intent"),
        "create failed internally": _turn("create_reminder", False, "create_failed"),
        "checkin failed": _turn("complete_reminder", False, "checkin_failed"),
        "gave up after clarifications": _turn("restart_flow", False, "restart_flow"),
        "pressed Vox, said nothing": _turn("briefing", True, "briefing"),
        "user cancelled": _turn("cancel_action", True, "cancelled"),
    }
    for label, turn in free.items():
        expect(not is_billable_turn(turn), f"must NOT charge: {label}")

    charged = {
        "no_match": _turn("delete_reminder", False, "no_match"),
        "time_mismatch": _turn("delete_reminder", False, "time_mismatch"),
        "medical advice refusal": _turn("medical_advice_refusal", True,
                                        "medical_advice_refusal"),
    }
    for label, turn in charged.items():
        expect(is_billable_turn(turn), f"must charge (user-caused): {label}")


async def case_29(s):
    """Premium window rolls in whole 30-day steps, keeping the reset day stable."""
    from datetime import datetime, timedelta, timezone
    from services.vox_quota import (PREMIUM_PERIOD, current_period_start,
                                    period_resets_at, premium_remaining)

    anchor = datetime(2026, 1, 14, 9, 0, tzinfo=timezone.utc)

    inside = current_period_start(anchor, anchor + timedelta(days=29))
    expect(inside == anchor, f"still in period 1: {inside}")

    nxt = current_period_start(anchor, anchor + timedelta(days=31))
    expect(nxt == anchor + PREMIUM_PERIOD, f"rolled one period: {nxt}")

    # Away for two months: reset day must not drift to "now".
    late = current_period_start(anchor, anchor + timedelta(days=75))
    expect(late == anchor + PREMIUM_PERIOD * 2,
           f"whole periods only, got {late}")
    expect((late - anchor).days % 30 == 0, "anchor must stay on the 30-day grid")
    expect(period_resets_at(late) == late + PREMIUM_PERIOD, "reset is start+30d")

    expect(premium_remaining(299) == 1, "299 used -> 1 left")
    expect(premium_remaining(300) == 0, "300 used -> blocked")
    expect(premium_remaining(400) == 0, "never negative")


async def case_30(s):
    """Trial allowance is 15 total, and the 16th is refused."""
    from services.vox_quota import TRIAL_VOX_LIMIT, trial_remaining

    expect(TRIAL_VOX_LIMIT == 15, f"trial limit is 15, got {TRIAL_VOX_LIMIT}")
    expect(trial_remaining(14) == 1, "14 used -> 1 left")
    expect(trial_remaining(15) == 0, "15 used -> exhausted")
    expect(trial_remaining(99) == 0, "never negative")


async def case_31(s):
    """The kill switch is absolute and defaults to on."""
    import os
    from services.subscription_service import vox_globally_enabled

    prior = os.environ.get("REMIVOX_ENABLED")
    try:
        os.environ.pop("REMIVOX_ENABLED", None)
        expect(vox_globally_enabled(), "defaults ON when unset")
        for off in ("false", "FALSE", "0", "no"):
            os.environ["REMIVOX_ENABLED"] = off
            expect(not vox_globally_enabled(), f"{off!r} must disable Vox")
        os.environ["REMIVOX_ENABLED"] = "true"
        expect(vox_globally_enabled(), "'true' re-enables")
    finally:
        if prior is None:
            os.environ.pop("REMIVOX_ENABLED", None)
        else:
            os.environ["REMIVOX_ENABLED"] = prior


async def case_32(s):
    """The test-mode bypass fails closed when the allowlist is empty."""
    import os
    from services.subscription_service import remivox_test_mode_enabled

    keys = ("REMIVOX_TEST_MODE", "REMIVOX_TEST_MODE_ALLOW_IN_PROD",
            "REMIVOX_TEST_MODE_UIDS", "APP_ENV")
    prior = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["REMIVOX_TEST_MODE"] = "true"
        os.environ["REMIVOX_TEST_MODE_ALLOW_IN_PROD"] = "true"
        os.environ["APP_ENV"] = "production"

        # The dangerous case: flag on, allowlist gone. Must NOT grant bypass —
        # otherwise one cleared env var disables quotas for every user.
        os.environ.pop("REMIVOX_TEST_MODE_UIDS", None)
        expect(not remivox_test_mode_enabled(firebase_uid="anyone"),
               "empty allowlist must deny bypass")
        os.environ["REMIVOX_TEST_MODE_UIDS"] = "   "
        expect(not remivox_test_mode_enabled(firebase_uid="anyone"),
               "whitespace-only allowlist must deny bypass")

        # Named testers still work — the workflow is preserved.
        os.environ["REMIVOX_TEST_MODE_UIDS"] = "tester-a,tester-b"
        expect(remivox_test_mode_enabled(firebase_uid="tester-a"),
               "named uid must still bypass")
        expect(not remivox_test_mode_enabled(firebase_uid="someone-else"),
               "unnamed uid must not bypass")
        expect(not remivox_test_mode_enabled(firebase_uid=None),
               "missing uid must not bypass")
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# RevenueCat: webhook semantics, auth and idempotency. Pure functions, no DB.
# ---------------------------------------------------------------------------

async def case_33(s):
    """Webhook auth fails closed and only accepts the configured token."""
    import os
    from services.revenuecat import webhook_authorized

    prior = os.environ.get("REVENUECAT_WEBHOOK_TOKEN")
    try:
        os.environ.pop("REVENUECAT_WEBHOOK_TOKEN", None)
        expect(not webhook_authorized("Bearer anything"),
               "no configured token must reject everything")
        os.environ["REVENUECAT_WEBHOOK_TOKEN"] = "s3cret-token"
        expect(not webhook_authorized(None), "missing header rejected")
        expect(not webhook_authorized(""), "empty header rejected")
        expect(not webhook_authorized("Bearer wrong"), "wrong token rejected")
        expect(webhook_authorized("Bearer s3cret-token"), "bearer form accepted")
        expect(webhook_authorized("s3cret-token"), "bare token accepted")
    finally:
        if prior is None:
            os.environ.pop("REVENUECAT_WEBHOOK_TOKEN", None)
        else:
            os.environ["REVENUECAT_WEBHOOK_TOKEN"] = prior


async def case_34(s):
    """Event types map to the right access transition."""
    from services.revenuecat import premium_after_event

    for kind in ("INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"):
        expect(premium_after_event(kind, {}) is True, f"{kind} must grant access")
    for kind in ("EXPIRATION", "REFUND"):
        expect(premium_after_event(kind, {}) is False, f"{kind} must revoke access")
    # CANCELLATION means "will not renew" — access runs to the paid-for date.
    # Revoking here would cut off time the user already paid for.
    expect(premium_after_event("CANCELLATION", {}) is None,
           "CANCELLATION must not revoke immediately")
    expect(premium_after_event("BILLING_ISSUE", {}) is None,
           "BILLING_ISSUE is a retry, not a revocation")
    expect(premium_after_event("SOMETHING_NEW", {}) is None,
           "unknown event types must not change the plan")


async def case_35(s):
    """Entitlement is read from RevenueCat's shape, expiry respected."""
    from datetime import datetime, timedelta, timezone
    from services.revenuecat import entitlement_active

    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    expect(entitlement_active({"entitlements": {"Premium": {"expires_date": future}}}),
           "unexpired Premium is active")
    expect(not entitlement_active({"entitlements": {"Premium": {"expires_date": past}}}),
           "expired Premium is not active")
    expect(entitlement_active({"entitlements": {"Premium": {"expires_date": None}}}),
           "lifetime entitlement is active")
    expect(not entitlement_active({"entitlements": {}}), "no entitlement -> inactive")
    expect(not entitlement_active({}), "empty subscriber -> inactive")


async def case_36(s):
    """An unverifiable claim must not upgrade anyone by default."""
    import os
    from services.revenuecat import allow_unverified, secret_key

    prior_key = os.environ.get("REVENUECAT_SECRET_KEY")
    prior_allow = os.environ.get("REVENUECAT_ALLOW_UNVERIFIED")
    try:
        os.environ.pop("REVENUECAT_SECRET_KEY", None)
        os.environ.pop("REVENUECAT_ALLOW_UNVERIFIED", None)
        expect(secret_key() is None, "no key configured")
        expect(not allow_unverified(),
               "unverified upgrades must be OFF unless explicitly enabled")
        os.environ["REVENUECAT_ALLOW_UNVERIFIED"] = "true"
        expect(allow_unverified(), "explicit opt-in is honoured")
    finally:
        for k, v in (("REVENUECAT_SECRET_KEY", prior_key),
                     ("REVENUECAT_ALLOW_UNVERIFIED", prior_allow)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def case_37(s):
    """B12 is spoken as "B twelve" in every written form the LLM produces."""
    from services.remivox.voice import normalize_for_speech

    # The failing form on device: the LLM preserves the user's hyphen.
    expect("B twelve" in normalize_for_speech("Vitamin B-12"),
           "hyphenated B-12 must be spoken")
    expect("b twelve" in normalize_for_speech("vitamin b12"),
           "lowercase b12 must be spoken")
    expect("B twelve" in normalize_for_speech("Vitamin B12"), "plain B12")
    expect("B twelve" in normalize_for_speech("Vitamin B 12"), "spaced B 12")
    got = normalize_for_speech("Vitamin B12 at 8:30 PM")
    expect(got == "Vitamin B twelve at eight thirty PM", f"full reply: {got!r}")
    # Must not mangle ordinary numbers.
    for untouched in ("Metoprolol 50", "Take 2 tablets"):
        expect(normalize_for_speech(untouched) == untouched,
               f"must not rewrite {untouched!r}")


async def case_38(s):
    """A not-yet-due reply states the fact and asks nothing unanswerable."""
    from services.remivox.response.builder import _text_for
    from services.remivox.intents.models import VoxIntent
    from services.remivox.actions.types import ActionResult

    payload = {"title": "Metoprolol", "due_local": "tomorrow at 10 PM"}
    text = _text_for("not_due_yet", VoxIntent.COMPLETE_REMINDER, payload,
                     ActionResult(success=False, action="complete_reminder",
                                  message_key="not_due_yet", payload=payload))
    expect("tomorrow at 10 PM" in text, f"must say when it IS due: {text!r}")
    expect("?" not in text,
           f"must not ask a question nothing can answer: {text!r}")


async def case_39(s):
    """Bare "yes" with nothing pending says so, rather than generic help."""
    out = await say("Yes", session=s)
    text = out["text"].lower()
    expect("waiting for confirmation" in text, f"reply={out['text']!r}")
    expect("i can set reminders" not in text,
           f"must not fall back to generic help: {out['text']!r}")


async def case_40(s):
    """"weekdays" and "weekends" map to the right custom_days."""
    out = await say("Set a reminder for Metoprolol at 8 AM on weekdays", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.recurrence == "custom", f"recurrence={r.recurrence}")
    expect(r.custom_days == ["monday", "tuesday", "wednesday", "thursday", "friday"],
           f"weekdays -> {r.custom_days}")

    STORE.reset()
    out = await say("Add Vitamin D at 9 AM on weekends", session=s + "-we")
    r = only_created()
    expect(r.recurrence == "custom", f"recurrence={r.recurrence}")
    expect(r.custom_days == ["saturday", "sunday"], f"weekends -> {r.custom_days}")


async def case_41(s):
    """An unsupported spacing must not become a nonsensical recurrence."""
    out = await say("Set a reminder for Metoprolol at 8 AM every other day", session=s)
    if STORE.created:
        r = STORE.created[0]
        # If it created anything, it must not have invented a schedule: 48h in
        # interval_hours, or weekday sets the user never named.
        expect(r.recurrence != "interval" or (r.interval_hours or 0) <= 24,
               f"must not store an out-of-range interval: {r.interval_hours}")
        expect(r.recurrence != "custom" or not r.custom_days,
               f"must not invent custom_days: {r.custom_days}")
    else:
        expect("?" in out["text"] or "how often" in out["text"].lower(),
               f"should ask rather than guess: {out['text']!r}")


async def case_42(s):
    """interval_count reaches the DB and the next occurrence is 48h, not 24h."""
    from datetime import datetime, timezone
    from services.db_reminders import next_recurrence_time

    out = await say("Set a reminder for Metoprolol at 8 AM every other day", session=s)
    expect(out["intent"] == "CREATE_REMINDER", f"intent={out['intent']}")
    r = only_created()
    expect(r.recurrence == "daily", f"recurrence={r.recurrence}")
    expect(r.interval_count == 2, f"interval_count={r.interval_count}")
    expect(local_hhmm(r.scheduled_time) == "08:00", f"time={local_hhmm(r.scheduled_time)}")

    # The safety property: the NEXT dose must be 48h out, not 24h.
    nxt = next_recurrence_time(r.scheduled_time, r.recurrence,
                               interval_count=r.interval_count)
    gap = (nxt - r.scheduled_time).total_seconds() / 3600
    expect(gap == 48, f"next dose {gap}h after this one, expected 48")


async def case_43(s):
    """Ambiguous or unstorable frequencies are asked about, never guessed."""
    for phrase in ("biweekly", "every few days"):
        STORE.reset()
        out = await say(f"Set a reminder for Metoprolol at 8 AM {phrase}",
                        session=f"{s}-{phrase[:6]}")
        if STORE.created:
            r = STORE.created[0]
            # If it created anything it must not have invented a spacing.
            expect(r.interval_count == 1 and r.recurrence in {"daily", "once"},
                   f"{phrase!r} invented recurrence={r.recurrence} "
                   f"interval_count={r.interval_count}")
        else:
            expect("?" in out["text"], f"{phrase!r} should ask: {out['text']!r}")


async def case_44(s):
    """An existing row without interval_count behaves exactly as before."""
    from datetime import datetime, timezone
    from services.db_reminders import next_recurrence_time

    t = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    for recurrence, expected_days in (("daily", 1), ("weekly", 7)):
        legacy = next_recurrence_time(t, recurrence)  # no interval_count at all
        explicit = next_recurrence_time(t, recurrence, interval_count=1)
        expect((legacy - t).days == expected_days,
               f"legacy {recurrence} moved {(legacy - t).days}d")
        expect(legacy == explicit, "absent interval_count must equal 1")


async def case_45(s):
    """"Marked on X" is a completion, not a request to create a reminder."""
    from services.remivox.intents.router import _correct_stt

    expect(_correct_stt("Marked on vitamin D3") == "mark done vitamin D3",
           "STT phrase correction must fire")

    out = await say("Mark done Metoprolol", session=s, reminders=reminders_fixture())
    expect(STORE.completed == ["r-metoprolol"], f"completed={STORE.completed}")
    text = out["text"].lower()
    expect("what time" not in text and "time should i" not in text,
           f"a completion must never ask for a time: {out['text']!r}")


async def case_46(s):
    """Mark done on an unresolvable title says so, and does not ask for a time."""
    out = await say("I took my Ibuprofen", session=s, reminders=reminders_fixture())
    expect(not STORE.completed, f"must not complete something else: {STORE.completed}")
    text = out["text"].lower()
    expect("what time" not in text, f"must not ask for a time: {out['text']!r}")
    expect("couldn't find" in text or "could not find" in text or "which" in text,
           f"should say it could not find it: {out['text']!r}")



# ---------------------------------------------------------------------------
# Defects found on iOS device testing (shared backend, so Android had them too).
# The fixture below is the reported account: a twice-daily medication produces
# several rows with the same title, which is the normal state for anyone on a
# morning-and-evening dose — not test pollution, and not an edge case.
# ---------------------------------------------------------------------------

def _hours_ago(hours: float):
    """A UTC timestamp that is reliably in the past, whatever time it is now."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _spoken(dt) -> str:
    """How the reply will read the time back — same formatter the builder uses."""
    from zoneinfo import ZoneInfo

    from services.remivox.response.builder import _speak_time

    return _speak_time(dt.astimezone(ZoneInfo(TZ)).strftime("%H:%M")).lower()


def twice_daily_fixture() -> dict:
    """
    Two Metoprolol doses at different times, plus a second medication.

    Anchored to "hours ago" rather than fixed wall-clock times. With fixed
    times the doses were only in the past for part of the day, so completing
    them hit the not-due guard and these cases failed depending on when the
    suite ran — which is worse than not having them.
    """
    return {
        "today": [
            {"id": "r-met-0800", "title": "Metoprolol", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _hours_ago(6)},
            {"id": "r-met-1445", "title": "Metoprolol", "reminder_type": "medication",
             "status": "pending", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _hours_ago(3)},
            {"id": "r-lyrica", "title": "Lyrica", "reminder_type": "medication",
             "status": "snoozed", "recurrence": "daily", "timezone": TZ,
             "scheduled_time": _hours_ago(1)},
        ],
        "upcoming": [],
        "past": [],
    }


async def case_47(s):
    """Two same-titled doses -> asks which, and names the times that differ."""
    out = await say("mark done Metoprolol", session=s, reminders=twice_daily_fixture())
    expect(not STORE.completed, f"must not guess a dose: {STORE.completed}")
    text = out["text"].lower()
    expect("which" in text, f"should ask which: {out['text']!r}")
    # Naming them both "Metoprolol" makes the question unanswerable.
    fx = twice_daily_fixture()
    first = _spoken(fx["today"][0]["scheduled_time"])
    second = _spoken(fx["today"][1]["scheduled_time"])
    expect(first in text and second in text,
           f"question must name both times ({first}, {second}): {out['text']!r}")


async def case_48(s):
    """The answer to that question is heard, and completes the named dose."""
    fx = twice_daily_fixture()
    await say("mark done Metoprolol", session=s, reminders=fx)
    # Say the time the earlier dose is actually at, whenever the suite runs.
    spoken = _spoken(fx["today"][0]["scheduled_time"])
    await say(f"the {spoken} one", session=s, reminders=fx)
    expect(STORE.completed == ["r-met-0800"],
           f"expected the earlier dose ({spoken}), got {STORE.completed}")


async def case_49(s):
    """A stated action is a NEW instruction, never an answer to the question."""
    fx = twice_daily_fixture()
    await say("mark done Metoprolol", session=s, reminders=fx)
    await say("mark done Lyrica", session=s, reminders=fx)
    # The worst outcome this app can produce is logging a drug the patient did
    # not take, so the pending Metoprolol must be untouched.
    expect("r-met-0800" not in STORE.completed and "r-met-1445" not in STORE.completed,
           f"pending Metoprolol must not be swallowed: {STORE.completed}")
    expect(STORE.completed == ["r-lyrica"],
           f"expected Lyrica only, got {STORE.completed}")


async def case_50(s):
    """The same instruction with nothing pending behaves identically."""
    out = await say("mark done Lyrica", session=s, reminders=twice_daily_fixture())
    expect(STORE.completed == ["r-lyrica"],
           f"expected Lyrica, got {STORE.completed} (reply={out['text']!r})")


async def case_51(s):
    """A completion that matches no title says so and completes nothing.

    Regression for the real defect: "is a medication" (+1) and "is due now" (+1)
    reached min_score with zero title evidence, so any unmatched name resolved
    to whichever medication was due.
    """
    out = await say("mark done Warfarin", session=s, reminders=twice_daily_fixture())
    expect(not STORE.completed,
           f"must complete nothing, completed {STORE.completed}")
    text = out["text"].lower()
    expect("couldn't find" in text or "could not find" in text,
           f"should say it could not find it: {out['text']!r}")


async def case_52(s):
    """"every other day" needs no frequency question."""
    out = await say("Set a reminder for Metoprolol at 8 AM every other day", session=s)
    r = only_created()
    expect(r.recurrence == "daily" and r.interval_count == 2,
           f"recurrence={r.recurrence} interval_count={r.interval_count}")
    expect("how often" not in out["text"].lower()
           and "just once" not in out["text"].lower(),
           f"must not ask the frequency it was told: {out['text']!r}")


async def case_53(s):
    """A stated date is the occurrence — do not ask how often it repeats."""
    out = await say("Set a reminder to take my pills today at 2:31 PM", session=s)
    expect("just once" not in out["text"].lower()
           and "how often" not in out["text"].lower(),
           f"a stated date already answered this: {out['text']!r}")
    r = only_created()
    expect(r.recurrence == "once", f"recurrence={r.recurrence}")
    expect(local_hhmm(r.scheduled_time) == "14:31",
           f"time={local_hhmm(r.scheduled_time)}")


async def case_54(s):
    """"biweekly" asks which meaning it is, and creates nothing meanwhile."""
    out = await say("Set a reminder for Metoprolol at 8 AM biweekly", session=s)
    expect(not STORE.created, f"must not guess a schedule: {STORE.created}")
    text = out["text"].lower()
    expect("twice a week" in text and "two weeks" in text,
           f"should offer both readings: {out['text']!r}")


async def case_55(s):
    """"as needed" is explained, not treated as a mishearing."""
    out = await say("Remind me to take Tylenol as needed", session=s)
    expect(not STORE.created, f"must not create: {STORE.created}")
    text = out["text"].lower()
    expect("didn't catch" not in text and "did not catch" not in text,
           f"user was understood; must not claim otherwise: {out['text']!r}")
    expect("as-needed" in text or "as needed" in text or "set times" in text,
           f"should explain what it cannot do: {out['text']!r}")


async def case_56(s):
    """A spoken "task" sets the type even when the subject is an appointment."""
    await say("create a task for Doctor appointment tomorrow at 10 AM", session=s)
    r = only_created()
    expect(str(r.reminder_type) == "task", f"reminder_type={r.reminder_type}")


async def case_57(s):
    """An appointment is never filed as a medication dose."""
    await say("Doctor appointment with Doctor Gubbala on Thursday at 3 PM", session=s)
    r = only_created()
    expect(str(r.reminder_type) == "appointment",
           f"reminder_type={r.reminder_type}")
    expect(str(r.reminder_type) != "medication", "a visit is not a dose")


async def case_58(s):
    """The instruction phrase is stripped from the title, mistranscribed or not.

    "tax to pick up lab results" is what speech recognition makes of "task
    to ..."; both reached the database with the action phrase in the title.
    """
    for phrase in ("Set a task to pick up lab results tomorrow at 10 AM",
                   "tax to pick up lab results tomorrow at 10 AM"):
        STORE.reset()
        await say(phrase, session=f"{s}-{phrase[:6]}")
        r = only_created()
        title = str(r.title).lower()
        expect("tax" not in title and not title.startswith("task"),
               f"{phrase!r} -> title={r.title!r}")
        expect("pick up lab results" in title, f"{phrase!r} -> title={r.title!r}")


CASES = [
    ("01 create daily medication", case_01),
    ("02 create daily, spoken 8:30 PM", case_02),
    ("03 create every-4-hours interval", case_03),
    ("04 create custom weekdays", case_04),
    ("05 create task tomorrow", case_05),
    ("06 create appointment on Thursday", case_06),
    ("07 incomplete: asks time only", case_07),
    ("08 incomplete: asks which day", case_08),
    ("09 incomplete: asks which medicine", case_09),
    ("10 multi-turn create, nothing re-asked", case_10),
    ("11 mark done -> one recurring instance", case_11),
    ("12 snooze 30 minutes", case_12),
    ("13 update time only", case_13),
    ("14 delete named reminder", case_14),
    ("15 read today's medications", case_15),
    ("16 read doctor summary", case_16),
    ("17 confirm with nothing pending", case_17),
    ("18 garbled transcript", case_18),
    ("19 'delete everything' is not bulk delete", case_19),
    ("20 STT artifacts corrected", case_20),
    ("21 clarification limit restarts the flow", case_21),
    ("22 mark done due today -> one next instance", case_22),
    ("23 mark done due tomorrow -> refused", case_23),
    ("24 colliding titles + time -> right one", case_24),
    ("25 colliding titles, no time -> asks", case_25),
    ("26 stated time matches nothing -> says so", case_26),
    ("27 multi-turn intent costs one use", case_27),
    ("28 our failures free, user refusals charged", case_28),
    ("29 premium window rolls in whole 30d steps", case_29),
    ("30 trial allowance is 15", case_30),
    ("31 kill switch absolute, defaults on", case_31),
    ("32 test-mode bypass fails closed", case_32),
    ("33 webhook auth fails closed", case_33),
    ("34 event types map to right transition", case_34),
    ("35 entitlement expiry respected", case_35),
    ("36 unverified upgrades off by default", case_36),
    ("37 B12 spoken in every written form", case_37),
    ("38 not-due reply asks nothing unanswerable", case_38),
    ("39 bare yes with nothing pending", case_39),
    ("40 weekdays / weekends map to custom_days", case_40),
    ("41 unsupported spacing not stored as nonsense", case_41),
    ("42 every other day -> interval_count=2, 48h gap", case_42),
    ("43 ambiguous frequency asks, never guesses", case_43),
    ("44 legacy rows unchanged without interval_count", case_44),
    ("45 'marked on X' completes, never creates", case_45),
    ("46 unresolvable mark-done says so", case_46),
    ("47 same-titled doses -> asks which, names times", case_47),
    ("48 answering 'the 8 AM one' completes that dose", case_48),
    ("49 stated action is not an answer to the question", case_49),
    ("50 mark done by name with nothing pending", case_50),
    ("51 unmatched name completes nothing, says so", case_51),
    ("52 every other day asks no frequency question", case_52),
    ("53 a stated date implies once", case_53),
    ("54 biweekly asks which meaning", case_54),
    ("55 as needed is explained, not misheard", case_55),
    ("56 spoken 'task' sets the type", case_56),
    ("57 appointment never filed as medication", case_57),
    ("58 instruction phrase stripped from title", case_58),
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nlu", choices=("mock", "llm", "regex"), default="mock")
    parser.add_argument("--only", nargs="*", type=int, default=None)
    args = parser.parse_args()

    if args.nlu == "mock":
        os.environ["REMIVOX_NLU"] = "llm"
        install_mock_nlu()
    else:
        os.environ["REMIVOX_NLU"] = args.nlu
        if args.nlu == "llm" and not os.getenv("GEMINI_API_KEY"):
            print("GEMINI_API_KEY is not set — live NLU run cannot proceed.")
            return 2

    print(f"Vox e2e suite — NLU mode: {args.nlu}\n" + "=" * 62)
    passed, failed = 0, []
    for index, (name, fn) in enumerate(CASES, start=1):
        if args.only and index not in args.only:
            continue
        STORE.reset()
        session = f"s{index}-{args.nlu}"
        clear_state(USER, session)
        try:
            await fn(session)
        except Failure as exc:
            failed.append((name, str(exc)))
            print(f"FAIL  {name}\n        {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {name}\n        {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"pass  {name}")

    print("=" * 62)
    print(f"{passed} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
