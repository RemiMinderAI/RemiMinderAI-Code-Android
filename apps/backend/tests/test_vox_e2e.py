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
import sys
import types
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


SUMMARIES = [
    {
        "title": "Visit with Dr. Gubbala",
        "summary_preview": "Blood pressure improved; continue Metoprolol.",
    }
]


def today() -> date:
    return datetime.now(timezone.utc).date()


def next_weekday(name: str) -> date:
    names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    wanted = names.index(name)
    base = today()
    delta = (wanted - base.weekday()) % 7
    return base + timedelta(days=delta or 7)


# ---------------------------------------------------------------------------
# Canned NLU responses (--nlu=mock) so the suite runs with no network.
# Keys are the exact transcripts below.
# ---------------------------------------------------------------------------

def _mock_table() -> dict[str, dict]:
    t = today().isoformat()
    tomorrow = (today() + timedelta(days=1)).isoformat()
    thursday = next_weekday("thursday").isoformat()
    return {
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
        "Set a reminder for Gonarex": {
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
        "I'm not sure": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Gonarex", "reminder_type": "medication"},
            "confidence": 0.6,
        },
        "Pregavelin at eighty thirty PM everyday": {
            "intent": "CREATE_REMINDER",
            "entities": {"title": "Pregabalin", "reminder_type": "medication",
                         "time": "20:30", "frequency": "daily"},
            "confidence": 0.85,
        },
    }


def install_mock_nlu() -> None:
    table = _mock_table()

    async def fake_call(user_message: str) -> dict:
        # The transcript is the last line of the composed user message.
        transcript = user_message.rsplit("Transcript: ", 1)[-1].strip()
        if transcript not in table:
            raise llm_extractor.ExtractionError(
                f"no canned NLU response for {transcript!r}"
            )
        return json.loads(json.dumps(table[transcript]))

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
    return await run_care_turn(
        user_uuid=USER,
        text=transcript,
        language="en",
        reminders=reminders or reminders_fixture(),
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
    expect(local_date(r.scheduled_time) == next_weekday("thursday").isoformat(),
           f"date={local_date(r.scheduled_time)}")


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
    out = await say("Set a reminder for Gonarex", session=s)
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
    out = await say("Set a reminder for Gonarex", session=s)
    expect("time" in out["text"].lower(), f"round 1: {out['text']!r}")

    out = await say("I'm not sure", session=s)
    expect("time" in out["text"].lower(), f"round 2: {out['text']!r}")

    out = await say("I'm not sure", session=s)
    expect("start over" in out["text"].lower(),
           f"round 3 should restart: {out['text']!r}")
    expect(not STORE.created, "nothing may be created")

    # State was cleared: the next utterance starts a clean request.
    out = await say("Set a reminder for Metoprolol at 10 AM every day", session=s)
    expect(bool(STORE.created), "a complete command after restart must execute")


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
