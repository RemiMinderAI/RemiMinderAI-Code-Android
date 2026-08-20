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

from services.gemini_client import get_gemini_client, resolve_gemini_api_key
from services.remivox.intents.models import IntentResult, VoxIntent
from services.remivox.intents.router import route_intent

logger = logging.getLogger(__name__)

# gemini-2.0-flash and 2.5-flash are retired for new users; the API points
# callers at 3.6-flash. Override per-environment with REMIVOX_NLU_MODEL.
DEFAULT_NLU_MODEL = "gemini-3.6-flash"
DEFAULT_TIMEOUT_SECONDS = 5.0


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
    "custom_days",
    "doctor_name",
    "new_time",
    "new_frequency",
    "time_ambiguous",
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
    "custom_days": [string] | null,
    "doctor_name": string | null,
    "new_time": "HH:MM" | null,
    "new_frequency": string | null,
    "time_ambiguous": boolean
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
   the current date provided in the user message.
7. For UPDATE intents, put the target reminder name in "title" and the new values
   in "new_time" / "new_frequency".
8. Multi-intent utterances: pick the PRIMARY intent.
9. Confirmations: "yes"/"yeah"/"correct"/"go ahead" -> intent=CONFIRM.
   "no"/"cancel that"/"never mind" -> intent=CANCEL.
10. "title" is the subject only — never include the time, day, or frequency in it.
    "Doctor appointment with Dr. Gubbala on Thursday at 3 PM" -> title="Doctor
    appointment with Dr. Gubbala", doctor_name="Dr. Gubbala", day_of_week="thursday".
11. A bare deletion phrase with no subject ("delete everything", "remove them all")
    is DELETE_REMINDER with title=null — never assume which reminder is meant.
12. If the transcript is empty, silent, or unintelligible, use intent=UNKNOWN with
    confidence <= 0.3. Do not guess a care action.
13. When PENDING CONTEXT is supplied, the transcript is an ANSWER to the question
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
        if value is not None and value != "" and value != []
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


def _clean_title(value: Any) -> Optional[str]:
    if value is None:
        return None
    title = re.sub(r"\s+", " ", str(value)).strip(" .,-")
    if len(title) < 2:
        return None
    return title[:80]


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
        "custom_days": _coerce_custom_days(raw_entities.get("custom_days")),
        "doctor_name": _clean_title(raw_entities.get("doctor_name")),
        "new_time": _coerce_time(raw_entities.get("new_time")),
        "new_frequency": _coerce_frequency(raw_entities.get("new_frequency")),
        "time_ambiguous": bool(raw_entities.get("time_ambiguous")),
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

async def _call_gemini(user_message: str) -> dict[str, Any]:
    api_key = resolve_gemini_api_key()
    if not api_key:
        raise ExtractionError("GEMINI_API_KEY not set")

    client = get_gemini_client(api_key)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.0,
        top_p=0.9,
        max_output_tokens=512,
    )

    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=_nlu_model(),
            contents=user_message,
            config=config,
        ),
        timeout=_timeout_seconds(),
    )

    raw = getattr(response, "text", None) or ""
    cleaned = _strip_code_fences(raw)
    if not cleaned:
        raise ExtractionError("LLM returned empty text")
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"LLM returned non-JSON: {exc}") from exc


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
        logger.info(
            "VOX_NLU llm intent=%s confidence=%.2f fields=%s latency_ms=%d",
            result["intent"],
            result["confidence"],
            _present_fields(result["entities"]),
            int((time.monotonic() - started) * 1000),
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
