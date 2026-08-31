"""
LLM entity extraction for RemiVox (Part 1).

One Gemini call does intent classification AND entity extraction, replacing the
regex router for natural speech ("every 4 hours", "maybe around 10 in the
morning", "Monday Wednesday and Friday").

The regex extractors are NOT deleted — they remain the fallback whenever Gemini
is slow, unavailable, or returns something unusable.

PHI: transcripts and entity VALUES are never logged. Only intent, confidence and
the NAMES of populated fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from google.genai import types
from google.genai.errors import ClientError

from services.gemini_client import get_gemini_client, resolve_gemini_api_key
from services.remivox.intents.extractors import normalize_spoken_title
from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.intents.router import route_intent

logger = logging.getLogger(__name__)

# gemini-2.0-flash and 2.5-flash are retired for new users; the API points
# callers at 3.6-flash. Override per-environment with REMIVOX_NLU_MODEL.
DEFAULT_NLU_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT_SECONDS = 15.0
# Thinking tokens share this budget with the visible JSON; 512 was exhausted
# mid-object. ~13 fields need far less than 2048, so this is pure headroom.
DEFAULT_MAX_OUTPUT_TOKENS = 2048


class ExtractionError(Exception):
    """Raised when the LLM produced nothing usable."""


# --- intent vocabulary -------------------------------------------------------

# The LLM speaks a slightly flatter vocabulary than VoxIntent (CONFIRM/CANCEL
# instead of CONFIRM_ACTION/CANCEL_ACTION); everything else maps 1:1.
_LLM_INTENT_TO_VOX: dict[str, VoxIntent] = {
    "CREATE_REMINDER": VoxIntent.CREATE_REMINDER,
    "UPDATE_REMINDER": VoxIntent.UPDATE_REMINDER,
    "DELETE_REMINDER": VoxIntent.DELETE_REMINDER,
    "COMPLETE_REMINDER": VoxIntent.COMPLETE_REMINDER,
    "SNOOZE_REMINDER": VoxIntent.SNOOZE_REMINDER,
    "SKIP_REMINDER": VoxIntent.SKIP_REMINDER,
    "READ_TODAY_MEDICATIONS": VoxIntent.READ_TODAY_MEDICATIONS,
    "READ_APPOINTMENTS": VoxIntent.READ_APPOINTMENTS,
    "READ_DOCTOR_SUMMARY": VoxIntent.READ_DOCTOR_SUMMARY,
    "CAREGIVER_BRIEF": VoxIntent.CAREGIVER_BRIEF,
    "CONFIRM": VoxIntent.CONFIRM_ACTION,
    "CANCEL": VoxIntent.CANCEL_ACTION,
    "MEDICAL_ADVICE_REFUSAL": VoxIntent.MEDICAL_ADVICE_REFUSAL,
    "HELP": VoxIntent.HELP,
    "UNKNOWN": VoxIntent.UNKNOWN,
}

_ENTITY_KEYS = (
    "title",
    "reminder_type",
    "time",
    "date",
    "day_of_week",
    "frequency",
    "interval_hours",
    "interval_count",
    "custom_days",
    "doctor_name",
    "new_time",
    "new_frequency",
    "time_ambiguous",
    "frequency_ambiguous",
    "schedule_unsupported",
    "minutes",
)

_VALID_FREQUENCIES = {
    "once",
    "daily",
    "weekly",
    "twice_daily",
    "interval",
    "custom",
}

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


SYSTEM_INSTRUCTION = """\
You are the NLU engine for a healthcare voice assistant serving elderly patients.
Analyze the transcript and return ONLY valid JSON (no markdown, no explanation):

{
  "intent": "CREATE_REMINDER" | "UPDATE_REMINDER" | "DELETE_REMINDER" | "COMPLETE_REMINDER" | "SNOOZE_REMINDER" | "SKIP_REMINDER" | "READ_TODAY_MEDICATIONS" | "READ_APPOINTMENTS" | "READ_DOCTOR_SUMMARY" | "CAREGIVER_BRIEF" | "CONFIRM" | "CANCEL" | "HELP" | "UNKNOWN",
  "entities": {
    "title": string | null,
    "reminder_type": "medication" | "appointment" | "task",
    "time": "HH:MM" | null,
    "date": "YYYY-MM-DD" | null,
    "day_of_week": string | null,
    "frequency": "once" | "daily" | "weekly" | "twice_daily" | "interval" | "custom" | null,
    "interval_hours": number | null,
    "interval_count": number | null,
    "custom_days": [string] | null,
    "doctor_name": string | null,
    "new_time": "HH:MM" | null,
    "new_frequency": string | null,
    "time_ambiguous": boolean,
    "frequency_ambiguous": string | null,
    "schedule_unsupported": boolean
  },
  "confidence": 0.0-1.0
}

RULES:
1. NEVER invent values. If the user did not state a time, time MUST be null.
2. NEVER default frequency. If not stated, frequency MUST be null.
3. "every 4 hours" -> frequency="interval", interval_hours=4. The "4" is NOT a time.
4. "at 4" with medication context -> time (use AM/PM context clues; bare hours 1-7
   with no meridiem in an evening-medication context lean PM, but if truly
   ambiguous set the time and also set "time_ambiguous": true).
5. Fix obvious STT errors from context: "Metropropol"->"Metoprolol",
   "Pregavelin"->"Pregabalin", "smooth"->"snooze" when clearly an action.
   Correct the drug name only when confident; otherwise keep what was heard.
6. Relative dates: "today", "tomorrow", "Thursday" -> resolve to YYYY-MM-DD using
   the current date provided in the user message. If the user did NOT name a day,
   "date" MUST be null — never fill in today's date as a guess. A time on its own
   ("at 10 AM") states no day. Only "today"/"tonight" state today.
   A bare weekday means the NEXT time that weekday occurs: if today is that
   weekday but the stated time has already passed, resolve to next week's date,
   never to a moment in the past.
7. For UPDATE intents, put the target reminder name in "title" and the new values
   in "new_time" / "new_frequency".
8. Multi-intent utterances: pick the PRIMARY intent.
9. Confirmations: "yes"/"yeah"/"correct"/"go ahead" -> intent=CONFIRM.
   "no"/"cancel that"/"never mind" -> intent=CANCEL.
9b. reminder_type: if the user SAYS "task" ("create a task", "set a task"),
    reminder_type="task" even when the subject mentions an appointment. If they
    say "appointment", or name a doctor/clinic/dentist visit, it is
    "appointment" — never "medication". Only use "medication" for a drug, a
    dose, a vitamin or a supplement.
10. "title" is the subject only — never include the time, day, or frequency in it.
    "Doctor appointment with Dr. Gubbala on Thursday at 3 PM" -> title="Doctor
    appointment with Dr. Gubbala", doctor_name="Dr. Gubbala", day_of_week="thursday".
10a. Strip the instruction phrase from the title. "Set a task to pick up lab
    results" -> title="pick up lab results", NOT "task to pick up lab results".
    The same for "remind me to", "set a reminder for", "create a task for",
    "I need to". Speech recognition also mangles the leading word — "tax to
    pick up..." is "task to pick up..." — so drop a leading "tax to" / "task
    to" the same way rather than keeping it in the title.
11c. FREQUENCY VOCABULARY. Map generously; users do not speak canonically.
    Every N days -> frequency="daily", interval_count=N:
      "every other day", "every second day", "alternate days", "on alternate
      days", "every two days", "every 2 days", "every 3 days", "every third
      day", "once every 3 days", "every 10 days".
    Every N weeks -> frequency="weekly", interval_count=N:
      "every other week", "every second week", "fortnightly", "every two
      weeks", "every 2 weeks", "every 3 weeks".
    Interval on a named day -> frequency="custom", custom_days=[that day],
      interval_count=N: "every other Monday", "every second Tuesday",
      "alternate Fridays".
    Named days -> frequency="custom", custom_days=[...]:
      "Mondays", "every Monday", "on Mondays", "Monday Wednesday Friday",
      "MWF", "Tuesdays and Thursdays", "Tues/Thurs".
    Weekday sets -> frequency="custom": "weekdays", "every weekday", "on
      weekdays", "Monday to Friday", "Mon-Fri", "working days" -> Mon..Fri;
      "weekends", "on weekends", "weekends only", "Saturday and Sunday" ->
      Sat,Sun.
    Daily variants -> frequency="daily", interval_count=1:
      "daily", "every day", "each day", "once a day", "once daily", "every
      single day".
    Hourly -> frequency="interval", interval_hours=N:
      "every 4 hours", "every four hours", "every 6 hours", "hourly"/"every
      hour" (interval_hours=1).
    Times of day imply DAILY but state no clock time: "every morning", "every
      night", "every evening", "mornings and evenings", "with breakfast", "at
      bedtime" -> frequency="daily", time=null. Still ask for the time.

11d. AMBIGUOUS FREQUENCIES — set frequency=null AND set frequency_ambiguous to
    the phrase the user said, so the assistant can ask about that specific
    ambiguity instead of asking a generic "how often?". Never pick the more
    likely reading; an ambiguous frequency is missing information, exactly like
    a missing time:
      "biweekly" -> frequency_ambiguous="biweekly" (twice a week, or every two weeks?)
      "bimonthly" -> frequency_ambiguous="bimonthly"
      "every few days", "a couple of times a week" -> frequency_ambiguous=<phrase>
      "monthly" -> frequency_ambiguous="monthly" (every 4 weeks, or the same date each month?)
      "four times a day" -> frequency_ambiguous="four times a day"
    Keep intent=CREATE_REMINDER and every other entity you did extract.

11f. NOT A SCHEDULE. "as needed", "PRN", "when I need it", "if I feel unwell"
    describe no recurrence at all. Keep intent=CREATE_REMINDER, extract the
    title, set frequency=null and schedule_unsupported=true. Do NOT use
    intent=UNKNOWN: the user was understood perfectly, so telling them they
    were not heard is wrong. The assistant explains it cannot set that.

11e. CANNOT BE STORED — set frequency=null rather than approximating:
    "every 90 minutes" and any sub-hour spacing (interval_hours is whole hours);
    "every other month"; "the 1st of each month". Rounding these silently would
    schedule a medication at the wrong frequency.

11a. Weekday sets: "weekdays" / "every weekday" / "on weekdays" / "Monday to
    Friday" -> frequency="custom", custom_days=["monday","tuesday","wednesday",
    "thursday","friday"]. "weekends" / "weekends only" / "on the weekend" ->
    frequency="custom", custom_days=["saturday","sunday"].
11. A bare deletion phrase with no subject ("delete everything", "remove them all")
    is DELETE_REMINDER with title=null — never assume which reminder is meant.
12. If the transcript is empty, silent, or unintelligible, use intent=UNKNOWN with
    confidence <= 0.3. Do not guess a care action.
13. When PENDING CONTEXT is supplied, the transcript is USUALLY an answer to the
    question in that context — but NOT when it plainly states a different
    action. "mark done X", "cancel X", "delete X", "snooze X" are new intents
    even mid-clarification; classify them as such and ignore the pending
    create. Treating "mark done vitamin D3" as the answer to "what time?" turns
    a completion into a reminder the user never asked for.
    Otherwise, the transcript is an ANSWER to the question
    in that context. Merge: repeat the values already in the pending context and
    add the newly stated one. A bare "8 PM" answering "what time?" means
    intent=CREATE_REMINDER with the pending title plus time="20:00".
"""


def _nlu_model() -> str:
    return (os.getenv("REMIVOX_NLU_MODEL") or DEFAULT_NLU_MODEL).strip()


def _timeout_seconds() -> float:
    try:
        return float(os.getenv("REMIVOX_NLU_TIMEOUT") or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _strip_code_fences(raw: str) -> str:
    """Defensive: strip ```json fences even though we ask for a JSON mime type."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _present_fields(entities: dict[str, Any]) -> list[str]:
    """Field NAMES only — values are PHI and must never be logged."""
    return sorted(
        key
        for key, value in (entities or {}).items()
        if value is not None and value is not False and value != "" and value != []
    )


def _local_now(timezone_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name or "UTC"))
    except Exception:
        return datetime.now(ZoneInfo("UTC"))


def _build_user_message(
    transcript: str,
    *,
    timezone_name: str,
    now: datetime,
    context: Optional[dict[str, Any]],
) -> str:
    weekday = now.strftime("%A")
    parts = [
        f"Today is {now.date().isoformat()} ({weekday}). User timezone: {timezone_name}.",
    ]
    pending_intent = (context or {}).get("pending_intent")
    pending_entities = (context or {}).get("pending_entities") or {}
    missing_slots = (context or {}).get("missing_slots") or []
    if pending_intent:
        parts.append(
            "PENDING CONTEXT — the assistant just asked a follow-up question and "
            "this transcript is the user's answer.\n"
            f"pending_intent: {pending_intent}\n"
            f"already_known: {json.dumps(pending_entities, default=str)}\n"
            f"still_missing: {json.dumps(missing_slots)}"
        )
    parts.append(f"Transcript: {transcript}")
    return "\n\n".join(parts)


# --- normalization -----------------------------------------------------------

def _coerce_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _TIME_RE.match(text):
        return text
    # Tolerate "8:00", "8:5", "0800"
    m = re.match(r"^(\d{1,2}):(\d{1,2})$", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    m = re.match(r"^(\d{2})(\d{2})$", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def _coerce_date(value: Any, *, today: date_cls) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if _DATE_RE.match(text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return text
    return None


def _coerce_weekday(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    for day in _WEEKDAYS:
        if text.startswith(day[:3]) and (text == day or text[:3] == day[:3]):
            return day
    return None


def _coerce_custom_days(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None
    days: list[str] = []
    for item in value:
        day = _coerce_weekday(item)
        if day and day not in days:
            days.append(day)
    return days or None


def _coerce_interval_hours(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        hours = int(float(value))
    except (TypeError, ValueError):
        return None
    if 1 <= hours <= 24:
        return hours
    return None


def _coerce_interval_count(value: Any) -> Optional[int]:
    """
    "Every N of the base period". Bounded to match the DB CHECK (1..60) so a
    mistranscribed number cannot schedule a reminder years out; out-of-range
    returns None, which makes the caller treat the frequency as unstated and
    ask, rather than silently storing something the user did not say.
    """
    if value is None:
        return None
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return None
    return count if 1 <= count <= 60 else None


def _coerce_frequency(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "twice": "twice_daily",
        "twice_a_day": "twice_daily",
        "two_times_daily": "twice_daily",
        "every_day": "daily",
        "everyday": "daily",
        "every_week": "weekly",
        "one_time": "once",
        "single": "once",
    }
    text = aliases.get(text, text)
    return text if text in _VALID_FREQUENCIES else None


# Instruction phrases a title should never keep. "tax to" is not a typo: it is
# how speech recognition renders "task to", and it reached the database as the
# stored title of a real reminder.
_TITLE_LEAD_RE = re.compile(
    r"^(?:please\s+)?(?:"
    r"set\s+(?:up\s+)?(?:a\s+|an\s+)?(?:new\s+)?(?:reminder|alarm|task|appointment)"
    r"(?:\s+(?:for|to|about))?"
    r"|create\s+(?:a\s+|an\s+)?(?:reminder|task|appointment)(?:\s+(?:for|to|about))?"
    r"|add\s+(?:a\s+|an\s+)?(?:reminder|task|appointment)(?:\s+(?:for|to|about))?"
    r"|remind\s+me\s+(?:to|about|that)"
    r"|(?:a\s+)?(?:task|tax)\s+(?:to|for|about)"
    r"|i\s+need\s+to"
    r")\s+",
    re.IGNORECASE,
)

# "Doctor." from a transcript is a title, not a sentence end. Normalising it to
# "Dr." keeps one spelling in the database instead of three.
# The lookahead is scoped case-SENSITIVE with (?-i:...): under IGNORECASE a
# bare [A-Z] also matches lowercase, which turned "Doctor appointment" into
# "Dr. appointment". Only a following capitalised word is a person's name.
_DOCTOR_RE = re.compile(r"\b(?:doctor|dr)\.?\s+(?=(?-i:[A-Z]))", re.IGNORECASE)


def _clean_phrase(value: Any) -> Optional[str]:
    """A short spoken phrase kept verbatim so the follow-up can quote it."""
    if value is None:
        return None
    phrase = re.sub(r"\s+", " ", str(value)).strip(" .,-")
    return phrase[:40] or None


def _clean_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    title = re.sub(r"\s+", " ", str(value)).strip(" .,-")
    # Repeat: "set a reminder to task to X" is rare but the artefacts stack.
    for _ in range(2):
        stripped = _TITLE_LEAD_RE.sub("", title, count=1).strip(" .,-")
        if stripped == title:
            break
        title = stripped
    title = _DOCTOR_RE.sub("Dr. ", title)
    # The regex fallback already did this; the LLM path did not, so titles kept
    # whatever case the transcript produced.
    title = normalize_spoken_title(title)
    if len(title) < 2:
        return None
    return title[:80]


# Words that settle reminder_type regardless of what the subject is about.
_EXPLICIT_TYPE = (
    ("task", re.compile(r"\b(?:a|the)?\s*task\b", re.IGNORECASE)),
    ("appointment", re.compile(r"\bappointments?\b", re.IGNORECASE)),
)


def apply_explicit_type(entities: dict[str, Any], transcript: str) -> dict[str, Any]:
    """
    Let a spoken "task" or "appointment" decide the type, ahead of the subject.

    "Create a task for Doctor appointment" is a task the user called a task; the
    word "appointment" is only what the task is about. Checked in order so the
    explicitly-requested container wins. Never downgrades to medication — a
    doctor visit misfiled as medication is the failure worth preventing.
    """
    text = transcript or ""
    for rtype, pattern in _EXPLICIT_TYPE:
        if pattern.search(text):
            entities["reminder_type"] = rtype
            return entities
    return entities


def normalize_extraction(
    payload: dict[str, Any],
    *,
    timezone_name: str = "UTC",
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Coerce raw LLM JSON into the strict shape the pipeline relies on."""
    if not isinstance(payload, dict):
        raise ExtractionError("LLM payload was not an object")

    raw_intent = str(payload.get("intent") or "").strip().upper()
    intent = _LLM_INTENT_TO_VOX.get(raw_intent)
    if intent is None:
        raise ExtractionError(f"Unknown intent from LLM: {raw_intent!r}")

    raw_entities = payload.get("entities")
    if not isinstance(raw_entities, dict):
        raw_entities = {}

    local_now = now or _local_now(timezone_name)
    today = local_now.date()

    reminder_type = str(raw_entities.get("reminder_type") or "").strip().lower()
    if reminder_type not in {"medication", "appointment", "task"}:
        reminder_type = "medication"

    entities: dict[str, Any] = {
        "title": _clean_title(raw_entities.get("title")),
        "reminder_type": reminder_type,
        "time": _coerce_time(raw_entities.get("time")),
        "date": _coerce_date(raw_entities.get("date"), today=today),
        "day_of_week": _coerce_weekday(raw_entities.get("day_of_week")),
        "frequency": _coerce_frequency(raw_entities.get("frequency")),
        "interval_hours": _coerce_interval_hours(raw_entities.get("interval_hours")),
        "interval_count": _coerce_interval_count(raw_entities.get("interval_count")),
        "custom_days": _coerce_custom_days(raw_entities.get("custom_days")),
        "doctor_name": _clean_title(raw_entities.get("doctor_name")),
        "new_time": _coerce_time(raw_entities.get("new_time")),
        "new_frequency": _coerce_frequency(raw_entities.get("new_frequency")),
        "time_ambiguous": bool(raw_entities.get("time_ambiguous")),
        "frequency_ambiguous": _clean_phrase(raw_entities.get("frequency_ambiguous")),
        "schedule_unsupported": bool(raw_entities.get("schedule_unsupported")),
        "minutes": None,
    }

    # Snooze minutes ride in as a plain number.
    minutes = raw_entities.get("minutes")
    try:
        entities["minutes"] = max(5, min(180, int(float(minutes)))) if minutes else None
    except (TypeError, ValueError):
        entities["minutes"] = None

    # Consistency repairs the model occasionally needs.
    if entities["interval_hours"] and entities["frequency"] != "interval":
        entities["frequency"] = "interval"
    if entities["custom_days"] and entities["frequency"] not in {"custom", "weekly"}:
        entities["frequency"] = "custom"
    if entities["frequency"] == "interval" and not entities["interval_hours"]:
        entities["frequency"] = None
    if entities["frequency"] == "custom" and not entities["custom_days"]:
        entities["frequency"] = None
    # An interval_count only means something on a period-based recurrence.
    # "every other day" is daily+2; on a one-off it is meaningless.
    if entities["interval_count"] and entities["frequency"] not in {
        "daily", "weekly", "custom"
    }:
        entities["interval_count"] = None
    # A count the model could not bound (e.g. "every few days") arrives as None
    # while frequency says daily — that would silently become plain daily, which
    # is MORE often than asked. Drop the frequency so the assistant asks.
    if (
        raw_entities.get("interval_count") is not None
        and entities["interval_count"] is None
        and entities["frequency"] in {"daily", "weekly", "custom"}
    ):
        entities["frequency"] = None

    if entities["custom_days"] and len(entities["custom_days"]) == 1:
        # "every Tuesday" is a weekly reminder, not a custom set.
        entities["frequency"] = "weekly"
        entities["day_of_week"] = entities["day_of_week"] or entities["custom_days"][0]

    try:
        confidence = float(payload.get("confidence", 0.8))
    except (TypeError, ValueError):
        confidence = 0.8
    confidence = max(0.0, min(1.0, confidence))

    return {
        "intent": intent.value,
        "entities": entities,
        "confidence": confidence,
        "source": "llm",
    }


# --- Gemini call -------------------------------------------------------------

# Thinking tokens are drawn from the same max_output_tokens budget as the visible
# answer, so a thinking model can spend the whole budget before it finishes
# emitting the JSON. Entity extraction is a structured transform, not a reasoning
# task, so we ask for the least thinking the model allows.
#
# The knob differs by model generation: Gemini 3.x accepts thinking_level and
# REJECTS thinking_budget=0 with 400 INVALID_ARGUMENT; 2.x accepts
# thinking_budget. Prefer level, fall back to budget, and if the server rejects
# whatever we sent, drop it for the rest of the process and retry once.
_thinking_supported = True


def _thinking_config() -> Optional[Any]:
    if not _thinking_supported:
        return None
    level = getattr(types, "ThinkingLevel", None)
    if level is not None and hasattr(level, "MINIMAL"):
        try:
            return types.ThinkingConfig(thinking_level=level.MINIMAL)
        except Exception:
            pass
    try:
        return types.ThinkingConfig(thinking_budget=0)
    except Exception:
        return None


def _max_output_tokens() -> int:
    try:
        return int(os.getenv("REMIVOX_NLU_MAX_TOKENS") or DEFAULT_MAX_OUTPUT_TOKENS)
    except (TypeError, ValueError):
        return DEFAULT_MAX_OUTPUT_TOKENS


def _build_config(*, with_thinking: bool) -> Any:
    kwargs: dict[str, Any] = {
        "system_instruction": SYSTEM_INSTRUCTION,
        "response_mime_type": "application/json",
        # Extraction must be deterministic: the same utterance extracting
        # differently between turns is a correctness bug, not a style choice.
        "temperature": 0.0,
        "top_p": 0.9,
        "max_output_tokens": _max_output_tokens(),
    }
    if with_thinking:
        thinking = _thinking_config()
        if thinking is not None:
            kwargs["thinking_config"] = thinking
    return types.GenerateContentConfig(**kwargs)


def _finish_reason(response: Any) -> Optional[str]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    return getattr(reason, "name", None) or str(reason)


def _redacted_tail(text: str, size: int = 40) -> str:
    """
    Structural shape of the response tail, with all content masked.

    The tail is what distinguishes "cut mid-string" from "genuinely malformed",
    but a truncated body routinely ends inside a medication name — so only JSON
    punctuation survives; every content character becomes a dot.
    """
    tail = (text or "")[-size:]
    return "".join(ch if ch in '{}[]":,' or ch.isspace() else "." for ch in tail)


async def _generate(client: Any, user_message: str, *, with_thinking: bool) -> Any:
    return await asyncio.wait_for(
        client.aio.models.generate_content(
            model=_nlu_model(),
            contents=user_message,
            config=_build_config(with_thinking=with_thinking),
        ),
        timeout=_timeout_seconds(),
    )


async def _call_gemini(user_message: str) -> dict[str, Any]:
    global _thinking_supported

    api_key = resolve_gemini_api_key()
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY not set")

    client = get_gemini_client(api_key)
    try:
        response = await _generate(client, user_message, with_thinking=True)
    except ClientError as exc:
        # The only argument we send that varies by model generation is the
        # thinking config; if the server rejects the request, drop it and retry
        # once rather than falling back to regex for the rest of the process.
        if _thinking_supported and "INVALID_ARGUMENT" in str(exc):
            logger.warning(
                "VOX_NLU thinking config rejected by model=%s — disabling and retrying",
                _nlu_model(),
            )
            _thinking_supported = False
            response = await _generate(client, user_message, with_thinking=False)
        else:
            raise

    finish_reason = _finish_reason(response)
    raw = getattr(response, "text", None) or ""
    cleaned = _strip_code_fences(raw)

    if not cleaned:
        raise ExtractionError(
            f"LLM returned empty text (finish_reason={finish_reason})"
        )
    try:
        parsed = json.loads(cleaned)
        # Attach token counts so unit cost is measured, not estimated. Counts
        # only — never prompt or response content. cached_content_token_count
        # matters: our system prompt is identical every call, so Gemini's
        # implicit caching bills much of the input at a reduced rate.
        meta = getattr(response, "usage_metadata", None)
        if meta is not None and isinstance(parsed, dict):
            parsed["_usage"] = {
                "prompt": getattr(meta, "prompt_token_count", None),
                "output": getattr(meta, "candidates_token_count", None),
                "cached": getattr(meta, "cached_content_token_count", None),
            }
        return parsed
    except json.JSONDecodeError as exc:
        if finish_reason == "MAX_TOKENS":
            logger.warning(
                "VOX_NLU llm truncated (finish_reason=MAX_TOKENS, budget=%d) "
                "— falling back to regex",
                _max_output_tokens(),
            )
        logger.warning(
            "VOX_NLU llm JSON parse failed: finish_reason=%s len=%d tail=%r",
            finish_reason,
            len(cleaned),
            _redacted_tail(cleaned),
        )
        raise ExtractionError(
            f"LLM returned non-JSON (finish_reason={finish_reason}): {exc}"
        ) from exc


async def extract_care_intent(
    transcript: str,
    context: Optional[dict[str, Any]] = None,
    *,
    timezone_name: str = "UTC",
    language: str = "en",
) -> dict[str, Any]:
    """
    Single Gemini call that does BOTH intent classification AND entity extraction.

    context carries pending conversation state for multi-turn flows:
      {"pending_intent": str, "pending_entities": dict, "missing_slots": list}

    Returns a validated dict {intent, entities, confidence, source}.
    Falls back to the regex extractors on timeout/error rather than failing the turn.
    """
    text = (transcript or "").strip()
    if not text:
        return {
            "intent": VoxIntent.UNKNOWN.value,
            "entities": {},
            "confidence": 0.0,
            "source": "empty",
        }

    now = _local_now(timezone_name)
    started = time.monotonic()
    try:
        payload = await _call_gemini(
            _build_user_message(
                text,
                timezone_name=timezone_name,
                now=now,
                context=context,
            )
        )
        result = normalize_extraction(payload, timezone_name=timezone_name, now=now)
        # A spoken "task"/"appointment" settles the type deterministically, so a
        # doctor visit cannot land in the database as a medication dose.
        apply_explicit_type(result["entities"], text)
        # Token counts, so unit cost is measured rather than estimated.
        # Counts only — no prompt or response content (PHI).
        usage = payload.get("_usage") if isinstance(payload, dict) else None
        logger.info(
            "VOX_NLU llm intent=%s confidence=%.2f fields=%s latency_ms=%d "
            "tokens_in=%s tokens_out=%s tokens_cached=%s",
            result["intent"],
            result["confidence"],
            _present_fields(result["entities"]),
            int((time.monotonic() - started) * 1000),
            (usage or {}).get("prompt", "?"),
            (usage or {}).get("output", "?"),
            (usage or {}).get("cached", "?"),
        )
        return result
    except asyncio.TimeoutError:
        logger.warning(
            "VOX_NLU llm timeout after %.1fs — falling back to regex",
            _timeout_seconds(),
        )
    except ExtractionError as exc:
        logger.warning("VOX_NLU llm unusable (%s) — falling back to regex", exc)
    except Exception as exc:  # network, auth, SDK changes
        logger.warning("VOX_NLU llm failed (%s) — falling back to regex", type(exc).__name__)

    return regex_fallback(text, context, timezone_name=timezone_name, language=language)


# --- regex fallback ----------------------------------------------------------

def regex_fallback(
    transcript: str,
    context: Optional[dict[str, Any]] = None,
    *,
    timezone_name: str = "UTC",
    language: str = "en",
) -> dict[str, Any]:
    """
    Deterministic extraction using the legacy regex router.

    Returns the same dict shape as extract_care_intent so callers do not branch.
    """
    ctx = context or {}
    result: IntentResult = route_intent(
        text=transcript,
        language=language,
        pending_intent=ctx.get("pending_intent"),
        pending_entities=ctx.get("pending_entities"),
    )

    legacy = dict(result.entities or {})
    entities: dict[str, Any] = {
        "title": legacy.get("title")
        or legacy.get("medication")
        or legacy.get("title_hint"),
        "reminder_type": legacy.get("reminder_type") or "medication",
        "time": legacy.get("time"),
        "date": None,
        "day_of_week": None,
        "frequency": _coerce_frequency(legacy.get("frequency")),
        "interval_hours": None,
        "interval_count": None,
        "custom_days": None,
        "doctor_name": None,
        "new_time": None,
        "new_frequency": None,
        "time_ambiguous": False,
        "minutes": legacy.get("minutes"),
    }

    intent = result.intent
    if intent == VoxIntent.UPDATE_REMINDER:
        entities["new_time"] = legacy.get("time")
        entities["new_frequency"] = _coerce_frequency(legacy.get("frequency"))
    if intent == VoxIntent.CLARIFY:
        # The regex router expresses "needs more info" as CLARIFY; the LLM path
        # expresses it as a complete intent plus validation. Unwrap so both paths
        # hand the pipeline the same thing.
        pending = dict(legacy.get("pending_entities") or {})
        entities["title"] = pending.get("title") or pending.get("medication")
        entities["time"] = pending.get("time")
        entities["frequency"] = _coerce_frequency(pending.get("frequency"))
        entities["reminder_type"] = pending.get("reminder_type") or "medication"
        intent = VoxIntent(str(legacy.get("pending_intent") or VoxIntent.CREATE_REMINDER.value))

    # The regex router hands back the raw spoken phrase; the LLM path cleans its
    # title and this one never did, so the instruction words survived into the
    # database. Same cleanup, same place in the flow.
    entities["title"] = _clean_title(entities.get("title"))
    apply_explicit_type(entities, transcript)

    out = {
        "intent": intent.value,
        "entities": entities,
        "confidence": result.confidence,
        "source": "regex",
        "legacy_entities": legacy,
    }
    logger.info(
        "VOX_NLU regex intent=%s confidence=%.2f fields=%s",
        out["intent"],
        out["confidence"],
        _present_fields(entities),
    )
    return out
