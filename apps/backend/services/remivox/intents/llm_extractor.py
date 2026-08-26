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
10. "title" is the subject only — never include the time, day, or frequency in it.
    "Doctor appointment with Dr. Gubbala on Thursday at 3 PM" -> title="Doctor
    appointment with Dr. Gubbala", doctor_name="Dr. Gubbala", day_of_week="thursday".
11a. Weekday sets: "weekdays" / "every weekday" / "on weekdays" / "Monday to
    Friday" -> frequency="custom", custom_days=["monday","tuesday","wednesday",
    "thursday","friday"]. "weekends" / "weekends only" / "on the weekend" ->
    frequency="custom", custom_days=["saturday","sunday"].
11b. Spacings we cannot store yet — "every other day", "every 3 days", "every
    other week", "every other Monday" — must NOT be forced into interval_hours
    or custom_days. Leave frequency null so the assistant asks, rather than
    silently creating a schedule the patient did not ask for.
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
